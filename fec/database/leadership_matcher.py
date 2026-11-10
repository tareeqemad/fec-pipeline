"""Load curated leader profile data."""
from __future__ import annotations

from typing import Any

import pandas as pd

from fec.cleaning.work.status import classify_employer_status, is_real_employer
from fec.cleaning.work.normalize import categorize_final
from fec.donor_match.rules import resolve_donor_key
from fec.database.loader.locations import select_location


# Match a roster donor by its saved or merged key.
def find_or_create_donor(
    cur: Any,
    donor_key: str,
    create_if_missing: bool,
    name: str,
    first: str,
    last: str,
) -> int:
    donor_key = donor_key.strip()
    if not donor_key:
        raise ValueError(f"Missing donor_key for {name!r}")

    # Follow merged keys so old roster keys create no duplicates.
    resolved_key = resolve_donor_key(donor_key)

    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (resolved_key,))
    row = cur.fetchone()
    if row:
        return row[0]

    if not create_if_missing:
        raise ValueError(
            f"Unknown donor_key {donor_key!r} for {name!r}; "
            "update the editorial CSV"
        )

    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (resolved_key, (first or "").strip().upper() or None, (last or "").strip().upper() or None),
    )
    return cur.fetchone()[0]


# Reuse an address or insert it with its coordinates.
def _address_id(cur: Any, fields: tuple, latitude: float | None, longitude: float | None) -> int:
    cur.execute(
        """
        SELECT address_id FROM addresses
        WHERE street_1   IS NOT DISTINCT FROM %s
          AND street_2   IS NOT DISTINCT FROM %s
          AND city       IS NOT DISTINCT FROM %s
          AND state_code IS NOT DISTINCT FROM %s
          AND zip_code   IS NOT DISTINCT FROM %s
        ORDER BY address_id
        LIMIT 1
        """,
        fields,
    )
    row = cur.fetchone()
    if row:
        address_id = row[0]
        if latitude is not None:
            cur.execute(
                "UPDATE addresses SET latitude=%s, longitude=%s "
                "WHERE address_id=%s AND latitude IS NULL",
                (latitude, longitude, address_id),
            )
        return address_id

    cur.execute(
        """
        INSERT INTO addresses
            (street_1, street_2, city, state_code, zip_code, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING address_id
        """,
        (*fields, latitude, longitude),
    )
    return cur.fetchone()[0]


# Link the curated address to its donor.
def upsert_donor_address(
    cur: Any, donor_id: int, street_1: str | None, street_2: str | None,
    city: str | None, state: str | None, zip_5: str | None = None,
    latitude: float | None = None, longitude: float | None = None,
) -> None:
    # Treat blank strings as missing.
    fields = tuple((value or "").strip() or None for value in (street_1, street_2, city, state, zip_5))
    if all(value is None for value in fields):
        return
    address_id = _address_id(cur, fields, latitude, longitude)

    cur.execute(
        "SELECT 1 FROM donor_addresses WHERE donor_id = %s AND address_id = %s LIMIT 1",
        (donor_id, address_id),
    )
    if cur.fetchone():
        return
    cur.execute(
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES (%s, %s)",
        (donor_id, address_id),
    )


# Same-city office, then a nearby state office, else default.
def employment_address_id(
    cur: Any, employer: str, locations: dict | None,
    state: str | None = "", zip_5: str | None = "",
    city: str | None = "",
) -> int | None:
    if not employer or not locations:
        return None
    location = select_location(locations.get(employer), zip_5 or "", state or "", city or "")
    if not location:
        return None

    fields = tuple(
        location.get(field) or None
        for field in ("employer_address", "", "employer_city", "employer_state", "employer_zip")
    )
    return _address_id(cur, fields, _coordinate(location.get("employer_latitude")),
                       _coordinate(location.get("employer_longitude")))


# Read an optional coordinate, ignoring invalid text.
def _coordinate(value) -> float | None:
    try:
        return float(value) if str(value or "").strip() else None
    except ValueError:
        return None


# Add roster employment only when the donor has none.
def upsert_leader_employment(cur: Any, donor_id: int, employer: str | None,
                             occupation: str | None = None, *,
                             state: str | None = "", zip_5: str | None = "",
                             city: str | None = "",
                             locations: dict | None = None) -> None:
    employer_name = (employer or '').strip()
    occupation = (occupation or '').strip() or None
    if not employer_name and not occupation:
        return

    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    if not employer_name or not is_real_employer(employer_name):
        employer_id = None
        status = classify_employer_status(employer_name) if employer_name else 'missing'
    else:
        cur.execute(
            "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (employer_name,),
        )
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (employer_name,))
        employer_id = cur.fetchone()[0]
        status = 'active'

    category_id = None
    if occupation:
        category = categorize_final(pd.Series([occupation])).iloc[0]
        cur.execute("SELECT occupation_category_id FROM occupation_categories WHERE name = %s", (category,))
        found = cur.fetchone()
        category_id = found[0] if found else None

    address_id = (
        employment_address_id(cur, employer_name, locations, state, zip_5, city)
        if status == 'active' else None
    )

    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, occupation_category_id,
             employer_status, address_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (donor_id, employer_id, occupation, category_id, status, address_id),
    )
