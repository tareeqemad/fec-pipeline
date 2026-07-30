"""Link leaders.csv rows to canonical donor rows for loader.py, matching by donor_key, then exact name, then creating the donor."""
from __future__ import annotations

import logging
from typing import Any

from fec.database.donor_match.keys import individual_donor_key
from fec.config.constants import SKIP_EMPLOYERS, REFUSAL_EMPLOYERS

logger = logging.getLogger(__name__)

# employer strings that are a life-status, not a company -> employer_status code
_STATUS_TO_CODE = {
    'RETIRED': 'retired',
    'NOT EMPLOYED': 'not_employed', 'UNEMPLOYED': 'not_employed',
    'SELF-EMPLOYED': 'self_employed', 'SELF EMPLOYED': 'self_employed',
    'SELF': 'self_employed',
}
_NON_EMPLOYER = SKIP_EMPLOYERS | REFUSAL_EMPLOYERS | set(_STATUS_TO_CODE)


def match_or_create_donor(
    cur: Any, name: str, first: str, last: str, city: str, state: str,
) -> tuple[int, str]:
    """Return (donor_id, match_method) for a leader, inserting a new donor row when nothing matches."""
    # 1. exact donor_key match
    donor_key = individual_donor_key(name, city, state)
    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (donor_key,))
    row = cur.fetchone()
    if row:
        return row[0], "donor_key_exact"

    # 2. exact name match; no nickname fuzz, leaders.csv aligns to FEC spelling
    cur.execute(
        """
        SELECT donor_id FROM donors
        WHERE entity_type = 'INDIVIDUAL'
          AND UPPER(first_name) = %s
          AND UPPER(last_name)  = %s
        """,
        ((first or "").strip().upper(), (last or "").strip().upper()),
    )
    matches = [row[0] for row in cur.fetchall()]

    if len(matches) == 1:
        return matches[0], "name_unique"

    if len(matches) > 1:
        # disambiguate by city/state, which live on addresses (donor_addresses
        # is only the link table)
        cur.execute(
            """
            SELECT DISTINCT d.donor_id
            FROM donors d
            JOIN donor_addresses da ON da.donor_id = d.donor_id
            JOIN addresses a ON a.address_id = da.address_id
            WHERE d.donor_id = ANY(%s)
              AND UPPER(a.city)       = %s
              AND UPPER(a.state_code) = %s
            """,
            (matches, (city or "").strip().upper(), (state or "").strip().upper()),
        )
        disambiguated = [row[0] for row in cur.fetchall()]
        if len(disambiguated) == 1:
            return disambiguated[0], "name_city_state"
        if len(disambiguated) > 1:
            logger.warning(
                "Leader %r matches %d donors in %s, %s - picking lowest id",
                name, len(disambiguated), city, state,
            )
            return min(disambiguated), "name_city_state"
        # name matches only in unrelated cities, likely different people
        logger.warning(
            "Leader %r has %d name matches but none in %s, %s - picking lowest id",
            name, len(matches), city, state,
        )
        return min(matches), "name_first_pick"

    # 3. create a new donor (leaders never in FEC)
    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (donor_key, (first or "").strip().upper() or None, (last or "").strip().upper() or None),
    )
    new_id = cur.fetchone()[0]
    logger.info("Created donor #%d (donor_key=%s) for leader %r", new_id, donor_key, name)
    return new_id, "created"


def upsert_donor_address(
    cur: Any, donor_id: int, street_1: str | None, street_2: str | None,
    city: str | None, state: str | None, zip_5: str | None = None,
    latitude: float | None = None, longitude: float | None = None,
) -> None:
    """Link a donor to a shared addresses row, inserting either as needed; empty strings become NULL and IS NOT DISTINCT FROM matches NULL == NULL like the loader's dedup."""
    def _none_if_empty(s: str | None) -> str | None:
        return s.strip() if s and s.strip() else None

    clean_street_1, clean_street_2 = _none_if_empty(street_1), _none_if_empty(street_2)
    clean_city, clean_state, clean_zip = (
        _none_if_empty(city), _none_if_empty(state), _none_if_empty(zip_5))
    if (clean_street_1 is None and clean_street_2 is None and clean_city is None
            and clean_state is None and clean_zip is None):
        return

    # 1. find the shared address row (full tuple match), else insert it
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
        # backfill coords if the shared row never got geocoded
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

    # 2. link the donor to that address (dedup the link)
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


def upsert_leader_employment(cur: Any, donor_id: int, employer: str | None) -> None:
    """Create an employment row from the CSV employer for a leader who has none; no-op when blank or when FEC-derived employment already exists (that stays authoritative)."""
    emp = (employer or '').strip()
    if not emp:
        return

    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    emp_upper = emp.upper()
    if emp_upper in _NON_EMPLOYER:
        employer_id = None
        status = _STATUS_TO_CODE.get(emp_upper, 'not_employed')
    else:
        # real company: reuse the row if it exists, else create it
        cur.execute(
            "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (emp,),
        )
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (emp,))
        employer_id = cur.fetchone()[0]
        status = 'active'

    # plain INSERT is safe (guard above returned on any existing row);
    # schema.sql's UNIQUE NULLS NOT DISTINCT backstops it
    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, employer_status)
        VALUES (%s, %s, NULL, %s)
        """,
        (donor_id, employer_id, status),
    )
