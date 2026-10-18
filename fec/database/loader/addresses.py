"""Shared address loading and links."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.loader._base import _count, to_float_or_none, to_native
from fec.env import EMPLOYER_LOCATIONS_CSV
from fec.log import get_logger
from fec.resolve.locations import PUBLISHABLE_ADDRESS_TRUST

logger = get_logger(__name__)

DONOR_ADDRESS_COLUMNS = [
    "contributor_street_1", "contributor_street_2", "contributor_city",
    "contributor_state", "contributor_zip",
]


# Convert missing and whitespace-only values to None.
def _blank_to_none(value):
    value = to_native(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


# Read publishable company locations and their primary office.
def load_employer_locations(frame: pd.DataFrame | None = None) -> list[dict]:
    if frame is None:
        if not EMPLOYER_LOCATIONS_CSV.exists():
            return []
        frame = pd.read_csv(
            EMPLOYER_LOCATIONS_CSV,
            dtype=str,
            keep_default_na=False,
        )
    locations = []
    for row in frame.to_dict("records"):
        street = _blank_to_none(row.get("employer_address"))
        city = _blank_to_none(row.get("employer_city"))
        # Allow town-only locations; skip rows with neither a street nor a town.
        if street is None and city is None:
            continue
        if row.get("address_trust") not in PUBLISHABLE_ADDRESS_TRUST:
            continue
        locations.append({
            "employer_name": row["employer_name"],
            "employer_address": street,
            "employer_city": city,
            "employer_state": _blank_to_none(row.get("employer_state")),
            "employer_zip": _blank_to_none(row.get("employer_zip")),
            "employer_latitude": row.get("employer_latitude"),
            "employer_longitude": row.get("employer_longitude"),
            "is_primary": str(row.get("is_primary")).lower() == "true",
            "method": "manual_override" if (
                row.get("address_source") == "manual"
                and row.get("address_trust") == "verified"
            ) else "",
        })

    grouped: dict[str, list[dict]] = {}
    for location in locations:
        grouped.setdefault(location["employer_name"], []).append(location)
    for employer_locations in grouped.values():
        if not any(location["is_primary"] for location in employer_locations):
            employer_locations[0]["is_primary"] = True
    return locations


# Convert missing values and '' to None; keep whitespace.
def _empty_to_none(value):
    value = to_native(value)
    return None if value == '' else value


# Use '' for missing address parts, matching SQL COALESCE.
def _akey(street_1, street_2, city, state, zip_code):
    return tuple(to_native(value) or '' for value in (street_1, street_2, city, state, zip_code))


# Load shared donor and company addresses.
def load_address_dimension(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict] | None = None,
) -> dict:
    logger.info("\n-- 4/8 Loading addresses (shared dimension) --")
    start = time.time()

    address_rows = _address_rows(df, _employer_locations(employer_locations))
    execute_values(cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(row) for row in address_rows.values()], page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)")

    cur.execute("""SELECT address_id, COALESCE(street_1,''), COALESCE(street_2,''),
                          COALESCE(city,''), COALESCE(state_code,''), COALESCE(zip_code,'')
                   FROM addresses""")
    address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    logger.info(f"  addresses: {_count(cur, 'addresses'):,} ({time.time()-start:.1f}s)")
    return address_ids


# Read the CSV for None; keep an explicit empty list.
def _employer_locations(employer_locations: list[dict] | None) -> list[dict]:
    if employer_locations is None:
        return load_employer_locations()
    return employer_locations


# Collect distinct donor and company addresses.
def _address_rows(df: pd.DataFrame, locations: list[dict]) -> dict:
    address_rows = {}
    for (street_1, street_2, city, state, zip_code), group in df.groupby(
        DONOR_ADDRESS_COLUMNS, dropna=False
    ):
        first_row = group.iloc[0]
        _add_address(address_rows, street_1, street_2, city, state, zip_code,
                     to_float_or_none(first_row.get('latitude')),
                     to_float_or_none(first_row.get('longitude')))

    for location in locations:
        _add_address(
            address_rows,
            location["employer_address"], None,
            location["employer_city"], location["employer_state"],
            location["employer_zip"],
            to_float_or_none(location.get("employer_latitude")),
            to_float_or_none(location.get("employer_longitude")),
        )
    return address_rows


# Add an address or fill its missing coordinates.
def _add_address(address_rows: dict, street_1, street_2, city, state, zip_code, latitude, longitude) -> None:
    parts = (street_1, street_2, city, state, zip_code)
    key = _akey(*parts)
    entry = address_rows.get(key)
    if entry is None:
        # '' and NULL share one key (_akey), so store the NULL form.
        address_rows[key] = [_empty_to_none(value) for value in parts] + [latitude, longitude]
    elif entry[5] is None and latitude is not None:
        entry[5], entry[6] = latitude, longitude


# Link donor addresses and return their IDs.
def load_donor_addresses(cur: Any, df: pd.DataFrame,
                         donor_ids: dict, address_ids: dict) -> dict:
    logger.info("\n-- 5/8 Loading donor addresses --")
    start = time.time()

    donor_address_rows = []
    for (donor_key, street_1, street_2, city, state, zip_code), _group in df.groupby(
        ['donor_key', *DONOR_ADDRESS_COLUMNS], dropna=False
    ):
        donor_id = donor_ids.get(donor_key)
        if not donor_id:
            continue
        address_id = address_ids.get(_akey(street_1, street_2, city, state, zip_code))
        if address_id is None:
            continue
        donor_address_rows.append((donor_id, address_id))

    execute_values(cur,
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s",
        donor_address_rows, page_size=5000)

    cur.execute("""
        SELECT da.donor_address_id, da.donor_id,
               COALESCE(a.street_1,''), COALESCE(a.street_2,''),
               COALESCE(a.city,''), COALESCE(a.state_code,''), COALESCE(a.zip_code,'')
        FROM donor_addresses da
        JOIN addresses a ON a.address_id = da.address_id
    """)
    donor_address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    logger.info(f"  donor_addresses: {_count(cur, 'donor_addresses'):,} ({time.time()-start:.1f}s)")
    return donor_address_ids


# Link each company to its primary address.
def link_employer_locations(
    cur: Any,
    address_ids: dict,
    get_employer_id,
    employer_locations: list[dict] | None = None,
) -> None:
    logger.info("\n-- 8/8 Linking employer locations --")
    start = time.time()

    employer_rows = _primary_location_rows(
        _employer_locations(employer_locations), address_ids, get_employer_id,
    )
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
    cur.execute("SELECT COUNT(*) FROM employers WHERE address_id IS NOT NULL")
    linked = cur.fetchone()[0]
    logger.info(f"  employers with location: {linked:,} ({time.time()-start:.1f}s)")
    _prune_orphan_addresses(cur)


# Match each primary office to company and address IDs.
def _primary_location_rows(locations, address_ids: dict, get_employer_id) -> list[tuple]:
    employer_rows = []
    for location in locations:
        if not location["is_primary"]:
            continue
        employer_id = get_employer_id(location["employer_name"])
        if not employer_id:
            raise RuntimeError(
                "employer location has no exact employer match: "
                f"{location['employer_name']!r}"
            )
        address_id = address_ids.get(_akey(
            location["employer_address"], None,
            location["employer_city"], location["employer_state"],
            location["employer_zip"],
        ))
        if address_id is None:
            raise RuntimeError(
                "employer location has no exact address match: "
                f"{location['employer_name']!r}"
            )
        employer_rows.append((int(employer_id), int(address_id)))
    return employer_rows


# Delete addresses with no remaining links.
def _prune_orphan_addresses(cur) -> None:
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM donor_employments de WHERE de.address_id = a.address_id)
    """)
    orphan_count = cur.rowcount
    if orphan_count:
        logger.info(f"  pruned {orphan_count:,} orphan addresses")
