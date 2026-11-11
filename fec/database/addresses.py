"""Load the addresses that donors and company offices share, and link donors and companies to them."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.connection import to_rows
from fec.database.offices import office_address

ADDRESS_COLUMNS = [
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
]


def address_key(parts: tuple) -> tuple:
    """Return street_1, street_2, city, state and zip code with '' for a missing part; address IDs use this key."""
    return tuple(part or "" for part in parts)


def load_addresses(cur: Any, df: pd.DataFrame, offices: list[dict]) -> dict:
    """Load each donor and office address once, add address_id to df and return the IDs by address key."""
    columns = [*ADDRESS_COLUMNS, "latitude", "longitude"]
    donor_addresses = df.drop_duplicates(columns).sort_values(ADDRESS_COLUMNS, kind="stable")
    from_filings = donor_addresses["sub_id"].notna()
    office_addresses = [(*office_address(office), office["latitude"], office["longitude"]) for office in offices]
    # An address keeps the first coordinates found for it: a filing's, then a company office's, then a roster's.
    in_order = [
        *to_rows(donor_addresses.loc[from_filings, columns]),
        *office_addresses,
        *to_rows(donor_addresses.loc[~from_filings, columns]),
    ]
    rows = {}
    for row in in_order:
        # Item 5 is the latitude.
        if address_key(row[:5]) not in rows or rows[address_key(row[:5])][5] is None:
            rows[address_key(row[:5])] = row

    execute_values(
        cur,
        "INSERT INTO addresses (street_1, street_2, city, state_code, zip_code, latitude, longitude) VALUES %s",
        list(rows.values()),
        page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)",
    )
    cur.execute("""
        SELECT address_id, COALESCE(street_1, ''), COALESCE(street_2, ''), COALESCE(city, ''),
               COALESCE(state_code, ''), COALESCE(zip_code, '')
        FROM addresses
    """)
    address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    df["address_id"] = [address_ids[address_key(parts)] for parts in to_rows(df[ADDRESS_COLUMNS])]
    return address_ids


def load_donor_addresses(cur: Any, df: pd.DataFrame) -> None:
    """Link each donor to each address they filed from and add donor_address_id to df."""
    columns = ["donor_key", *ADDRESS_COLUMNS]
    donor_addresses = df.drop_duplicates(columns).sort_values(columns)
    execute_values(
        cur,
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s",
        to_rows(donor_addresses[["donor_id", "address_id"]]),
        page_size=5000,
    )
    cur.execute("SELECT donor_address_id, donor_id, address_id FROM donor_addresses")
    donor_address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    df["donor_address_id"] = [donor_address_ids[link] for link in zip(df["donor_id"], df["address_id"])]


def link_employer_offices(cur: Any, offices: list[dict], employer_ids: dict, address_ids: dict) -> None:
    """Give each company the address of its primary office, then delete the office addresses nothing uses."""
    rows = [
        (employer_ids[office["employer_name"]], address_ids[address_key(office_address(office))])
        for office in offices
        if office["is_primary"]
    ]
    execute_values(
        cur,
        """
        UPDATE employers e
        SET address_id = v.address_id
        FROM (VALUES %s) AS v(employer_id, address_id)
        WHERE e.employer_id = v.employer_id
        """,
        rows,
        template="(%s::int, %s::int)",
        page_size=5000,
    )
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM donor_employments de WHERE de.address_id = a.address_id)
    """)
