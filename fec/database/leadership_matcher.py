"""Load curated leader profile data."""
from __future__ import annotations

from typing import Any

import pandas as pd

from fec.cleaning.occupations import _categorize_final

from fec.cleaning.previous_employer import (
    classify_employer_status,
    is_real_employer,
)
from fec.donor_match.rules import resolve_donor_key


def find_or_create_donor(
    cur: Any,
    donor_key: str,
    create_if_missing: bool,
    name: str,
    first: str,
    last: str,
) -> tuple[int, str]:
    """Use the curated donor key, after the identity merges the cleaning applied."""
    donor_key = donor_key.strip()
    if not donor_key:
        raise ValueError(f"Missing donor_key for {name!r}")

    # Cleaning moved a merged-away key's contributions to the surviving key, so a
    # roster still naming the old key must follow them instead of creating a twin.
    resolved_key = resolve_donor_key(donor_key)
    method = "donor_key_exact" if resolved_key == donor_key else "donor_key_merged"

    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (resolved_key,))
    row = cur.fetchone()
    if row:
        return row[0], method

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
    new_id = cur.fetchone()[0]
    return new_id, "created"


def upsert_donor_address(
    cur: Any, donor_id: int, street_1: str | None, street_2: str | None,
    city: str | None, state: str | None, zip_5: str | None = None,
    latitude: float | None = None, longitude: float | None = None,
) -> None:
    """Link one curated address."""
    def _none_if_empty(s: str | None) -> str | None:
        return s.strip() if s and s.strip() else None

    clean_street_1, clean_street_2 = _none_if_empty(street_1), _none_if_empty(street_2)
    clean_city, clean_state, clean_zip = (
        _none_if_empty(city), _none_if_empty(state), _none_if_empty(zip_5))
    if (clean_street_1 is None and clean_street_2 is None and clean_city is None
            and clean_state is None and clean_zip is None):
        return

    cur.execute(
        """
        SELECT address_id FROM addresses
        WHERE street_1   IS NOT DISTINCT FROM %s
          AND street_2   IS NOT DISTINCT FROM %s
          AND city       IS NOT DISTINCT FROM %s
          AND state_code IS NOT DISTINCT FROM %s
          AND zip_code   IS NOT DISTINCT FROM %s
        LIMIT 1
        """,
        (clean_street_1, clean_street_2, clean_city, clean_state, clean_zip),
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
    else:
        cur.execute(
            """
            INSERT INTO addresses
                (street_1, street_2, city, state_code, zip_code, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING address_id
            """,
            (clean_street_1, clean_street_2, clean_city, clean_state, clean_zip,
             latitude, longitude),
        )
        address_id = cur.fetchone()[0]

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


def upsert_leader_employment(cur: Any, donor_id: int, employer: str | None,
                             occupation: str | None = None) -> None:
    """Add the roster's employer/occupation only for a donor with no FEC employment (FEC wins)."""
    emp = (employer or '').strip()
    occ = (occupation or '').strip() or None
    if not emp and not occ:
        return

    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    if not emp or not is_real_employer(emp):
        employer_id = None
        status = classify_employer_status(emp) if emp else 'missing'
        if status == 'missing':
            status = 'not_employed'
    else:
        cur.execute(
            "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (emp,),
        )
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (emp,))
        employer_id = cur.fetchone()[0]
        status = 'active'

    occ_cat_id = None
    if occ:
        category = _categorize_final(pd.Series([occ])).iloc[0]
        cur.execute("SELECT occupation_category_id FROM occupation_categories WHERE name = %s", (category,))
        found = cur.fetchone()
        occ_cat_id = found[0] if found else None

    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, occupation_category_id, employer_status)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (donor_id, employer_id, occ, occ_cat_id, status),
    )
