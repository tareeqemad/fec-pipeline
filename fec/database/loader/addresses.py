"""Shared address loading and links."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.database.loader._base import to_float_or_none, to_native
from fec.database.loader.locations import PUBLISHABLE_ADDRESS_TRUST
from fec.env import EMPLOYER_LOCATIONS_CSV

DONOR_ADDRESS_COLUMNS = [
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
]


def _blank_to_none(value: Any) -> Any:
    """Convert missing and whitespace-only values to None."""
    value = to_native(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def load_employer_locations(frame: pd.DataFrame | None = None) -> list[dict]:
    """Read publishable company locations and their primary office."""
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
        # Allow town-only locations; skip rows with neither street nor town.
        if street is None and city is None:
            continue
        if row.get("address_trust") not in PUBLISHABLE_ADDRESS_TRUST:
            continue
        locations.append(
            {
                "employer_name": row["employer_name"],
                "employer_address": street,
                "employer_city": city,
                "employer_state": _blank_to_none(row.get("employer_state")),
                "employer_zip": _blank_to_none(row.get("employer_zip")),
                "employer_latitude": row.get("employer_latitude"),
                "employer_longitude": row.get("employer_longitude"),
                "is_primary": str(row.get("is_primary")).lower() == "true",
                "method": "manual_override"
                if (row.get("address_source") == "manual" and row.get("address_trust") == "verified")
                else "",
            }
        )

    grouped: dict[str, list[dict]] = {}
    for location in locations:
        grouped.setdefault(location["employer_name"], []).append(location)
    for employer_locations in grouped.values():
        if not any(location["is_primary"] for location in employer_locations):
            employer_locations[0]["is_primary"] = True
    return locations


def _empty_to_none(value: Any) -> Any:
    """Convert missing values and '' to None; keep whitespace."""
    value = to_native(value)
    return None if value == "" else value


def _akey(street_1: Any, street_2: Any, city: Any, state: Any, zip_code: Any) -> tuple:
    """Use '' for missing address parts, matching SQL COALESCE."""
    return tuple(to_native(value) or "" for value in (street_1, street_2, city, state, zip_code))


def load_address_dimension(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict] | None = None,
) -> dict:
    """Load shared donor and company addresses."""
    address_rows = _address_rows(df, _employer_locations(employer_locations))
    execute_values(
        cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(row) for row in address_rows.values()],
        page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)",
    )

    cur.execute("""SELECT address_id, COALESCE(street_1,''), COALESCE(street_2,''),
                          COALESCE(city,''), COALESCE(state_code,''), COALESCE(zip_code,'')
                   FROM addresses""")
    address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    return address_ids


def _employer_locations(employer_locations: list[dict] | None) -> list[dict]:
    """Read the CSV for None; keep an explicit empty list."""
    if employer_locations is None:
        return load_employer_locations()
    return employer_locations


def _address_rows(df: pd.DataFrame, locations: list[dict]) -> dict:
    """Collect distinct donor and company addresses."""
    address_rows = {}
    columns = DONOR_ADDRESS_COLUMNS + ["latitude", "longitude"]
    for (street_1, street_2, city, state, zip_code), group in df[columns].groupby(DONOR_ADDRESS_COLUMNS, dropna=False):
        first_row = group.iloc[0]
        _add_address(
            address_rows,
            street_1,
            street_2,
            city,
            state,
            zip_code,
            to_float_or_none(first_row.get("latitude")),
            to_float_or_none(first_row.get("longitude")),
        )

    for location in locations:
        _add_address(
            address_rows,
            location["employer_address"],
            None,
            location["employer_city"],
            location["employer_state"],
            location["employer_zip"],
            to_float_or_none(location.get("employer_latitude")),
            to_float_or_none(location.get("employer_longitude")),
        )
    return address_rows


def _add_address(
    address_rows: dict,
    street_1: Any,
    street_2: Any,
    city: Any,
    state: Any,
    zip_code: Any,
    latitude: float | None,
    longitude: float | None,
) -> None:
    """Add an address or fill its missing coordinates."""
    parts = (street_1, street_2, city, state, zip_code)
    key = _akey(*parts)
    entry = address_rows.get(key)
    if entry is None:
        # '' and NULL share one key, so store NULL.
        address_rows[key] = [_empty_to_none(value) for value in parts] + [latitude, longitude]
    elif entry[5] is None and latitude is not None:
        entry[5], entry[6] = latitude, longitude


def load_donor_addresses(cur: Any, df: pd.DataFrame, donor_ids: dict, address_ids: dict) -> dict:
    """Link donor addresses and return their IDs."""
    donor_address_rows = []
    columns = ["donor_key", *DONOR_ADDRESS_COLUMNS]
    for (donor_key, street_1, street_2, city, state, zip_code), _group in df[columns].groupby(columns, dropna=False):
        donor_id = donor_ids.get(donor_key)
        if not donor_id:
            continue
        address_id = address_ids.get(_akey(street_1, street_2, city, state, zip_code))
        if address_id is None:
            continue
        donor_address_rows.append((donor_id, address_id))

    execute_values(
        cur, "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s", donor_address_rows, page_size=5000
    )

    cur.execute("""
        SELECT da.donor_address_id, da.donor_id,
               COALESCE(a.street_1,''), COALESCE(a.street_2,''),
               COALESCE(a.city,''), COALESCE(a.state_code,''), COALESCE(a.zip_code,'')
        FROM donor_addresses da
        JOIN addresses a ON a.address_id = da.address_id
    """)
    donor_address_ids = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    return donor_address_ids


def link_employer_locations(
    cur: Any,
    address_ids: dict,
    get_employer_id: Callable[[str], int | None],
    employer_locations: list[dict] | None = None,
) -> None:
    """Link each company to its primary address."""
    employer_rows = _primary_location_rows(
        _employer_locations(employer_locations),
        address_ids,
        get_employer_id,
    )
    if employer_rows:
        execute_values(
            cur,
            """
            UPDATE employers e
            SET address_id = v.address_id
            FROM (VALUES %s) AS v(employer_id, address_id)
            WHERE e.employer_id = v.employer_id
        """,
            employer_rows,
            template="(%s::int, %s::int)",
            page_size=5000,
        )
    _prune_orphan_addresses(cur)


def _primary_location_rows(
    locations: list[dict], address_ids: dict, get_employer_id: Callable[[str], int | None]
) -> list[tuple]:
    """Match each primary office to company and address IDs."""
    employer_rows = []
    for location in locations:
        if not location["is_primary"]:
            continue
        employer_id = get_employer_id(location["employer_name"])
        if not employer_id:
            raise RuntimeError(f"employer location has no exact employer match: {location['employer_name']!r}")
        address_id = address_ids.get(
            _akey(
                location["employer_address"],
                None,
                location["employer_city"],
                location["employer_state"],
                location["employer_zip"],
            )
        )
        if address_id is None:
            raise RuntimeError(f"employer location has no exact address match: {location['employer_name']!r}")
        employer_rows.append((int(employer_id), int(address_id)))
    return employer_rows


def _prune_orphan_addresses(cur: Any) -> None:
    """Delete addresses with no remaining links."""
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM donor_employments de WHERE de.address_id = a.address_id)
    """)
