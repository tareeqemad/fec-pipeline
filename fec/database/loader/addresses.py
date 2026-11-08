"""Shared address dimension for donors, companies and the rosters."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.loader._base import to_float_or_none, to_native
from fec.database.loader.locations import office_address_key

DONOR_ADDRESS_COLUMNS = [
    "contributor_street_1", "contributor_street_2", "contributor_city",
    "contributor_state", "contributor_zip",
]


# Use '' for missing address parts, matching SQL COALESCE.
def address_key(street_1, street_2, city, state, zip_code):
    return tuple(to_native(value) or '' for value in (street_1, street_2, city, state, zip_code))


# Convert missing values and '' to None; keep whitespace.
def _empty_to_none(value):
    value = to_native(value)
    return None if value == '' else value


# Add an address or fill its missing coordinates.
def add_address(address_rows: dict, parts: tuple, latitude, longitude) -> None:
    key = address_key(*parts)
    entry = address_rows.get(key)
    if entry is None:
        # '' and NULL share one key, so store NULL.
        address_rows[key] = [_empty_to_none(value) for value in parts] + [latitude, longitude]
    elif entry[5] is None and latitude is not None:
        entry[5], entry[6] = latitude, longitude


# Load every address: donor filings, company offices, roster addresses.
def load_address_dimension(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict],
    roster: list[dict] = (),
) -> dict:
    address_rows: dict = {}
    columns = DONOR_ADDRESS_COLUMNS + ['latitude', 'longitude']
    for parts, group in df[columns].groupby(DONOR_ADDRESS_COLUMNS, dropna=False):
        first_row = group.iloc[0]
        add_address(address_rows, parts,
                    to_float_or_none(first_row.get('latitude')),
                    to_float_or_none(first_row.get('longitude')))
    for location in employer_locations:
        add_address(
            address_rows, office_address_key(location),
            to_float_or_none(location.get("employer_latitude")),
            to_float_or_none(location.get("employer_longitude")),
        )
    for person in roster:
        if person["address"]:
            add_address(address_rows, person["address"], person["latitude"], person["longitude"])

    execute_values(cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(row) for row in address_rows.values()], page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)")

    cur.execute("""SELECT address_id, COALESCE(street_1,''), COALESCE(street_2,''),
                          COALESCE(city,''), COALESCE(state_code,''), COALESCE(zip_code,'')
                   FROM addresses""")
    return {tuple(row[1:]): row[0] for row in cur.fetchall()}


# Link donors to their filed and roster addresses; return the link IDs.
def load_donor_addresses(cur: Any, df: pd.DataFrame, donor_ids: dict, address_ids: dict,
                         roster: list[dict] = ()) -> dict:
    links = []
    columns = ['donor_key', *DONOR_ADDRESS_COLUMNS]
    for (donor_key, *parts), _group in df[columns].groupby(columns, dropna=False):
        links.append((donor_ids.get(donor_key), address_ids.get(address_key(*parts))))
    for person in roster:
        if person["address"]:
            links.append((donor_ids[person["donor_key"]], address_ids.get(address_key(*person["address"]))))
    rows = list(dict.fromkeys(link for link in links if None not in link))

    execute_values(cur,
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s",
        rows, page_size=5000)

    cur.execute("""
        SELECT da.donor_address_id, da.donor_id,
               COALESCE(a.street_1,''), COALESCE(a.street_2,''),
               COALESCE(a.city,''), COALESCE(a.state_code,''), COALESCE(a.zip_code,'')
        FROM donor_addresses da
        JOIN addresses a ON a.address_id = da.address_id
    """)
    return {tuple(row[1:]): row[0] for row in cur.fetchall()}


# Link each company to its primary office, then drop unlinked addresses.
def link_employer_locations(
    cur: Any,
    address_ids: dict,
    get_employer_id,
    employer_locations: list[dict],
) -> None:
    employer_rows = []
    for location in employer_locations:
        if not location["is_primary"]:
            continue
        employer_id = get_employer_id(location["employer_name"])
        if not employer_id:
            raise RuntimeError(
                "employer location has no exact employer match: "
                f"{location['employer_name']!r}"
            )
        address_id = address_ids.get(office_address_key(location))
        if address_id is None:
            raise RuntimeError(
                "employer location has no exact address match: "
                f"{location['employer_name']!r}"
            )
        employer_rows.append((int(employer_id), int(address_id)))
    if employer_rows:
        execute_values(cur, """
            UPDATE employers e
            SET address_id = v.address_id
            FROM (VALUES %s) AS v(employer_id, address_id)
            WHERE e.employer_id = v.employer_id
        """,
        employer_rows,
        template="(%s::int, %s::int)",
        page_size=5000)
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM donor_employments de WHERE de.address_id = a.address_id)
    """)
