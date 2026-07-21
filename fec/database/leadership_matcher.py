"""
leadership_matcher.py — link leaders.csv rows to canonical donor rows.

Used by loader.py when populating the refactored `leaders` table
(which holds only donor_id + committee_ids). For each row in
leaders.csv we need a donor row to point at; this module finds the
right one or creates it.

Matching strategy (in order):
    1. Exact donor_key match (same name+city+state as in FEC) — covers
       the 79 leaders who already donated and were keyed by donor_match.
       Verified: all 86 current leadership names match FEC's spelling
       exactly, so no fuzzy expansion is needed.
    2. Exact name match (first_name + last_name) — fallback if the leader
       has a donor row but their leaders.csv city differs from FEC
       (e.g. they updated their address). Multiple candidates are
       disambiguated by city/state via donor_addresses.
    3. Insert a new donor row — covers leaders who never donated to FEC.
       Their donor_key comes from the shared `individual_donor_key` so it
       stays identical if they later appear in FEC contribution data.
"""
from __future__ import annotations

import logging
from typing import Any

from fec.database.donor_match.output import individual_donor_key
from fec.config.constants import SKIP_EMPLOYERS, REFUSAL_EMPLOYERS

logger = logging.getLogger(__name__)

# Employer strings that are really a life-status, not a company. Maps the
# CSV's leader_employer to the normalized employer_status the schema uses.
_STATUS_TO_CODE = {
    'RETIRED': 'retired',
    'NOT EMPLOYED': 'not_employed', 'UNEMPLOYED': 'not_employed',
    'SELF-EMPLOYED': 'self_employed', 'SELF EMPLOYED': 'self_employed',
    'SELF': 'self_employed',
}
_NON_EMPLOYER = SKIP_EMPLOYERS | REFUSAL_EMPLOYERS | set(_STATUS_TO_CODE)


def match_or_create_donor(
    cur: Any,
    name: str,
    first: str,
    last: str,
    city: str,
    state: str,
) -> tuple[int, str]:
    """Find an existing donor for this leader, or create one.

    Returns:
        (donor_id, match_method) where match_method is one of:
            "donor_key_exact"   — donor_key already in donors table
            "name_unique"       — unique exact-name match
            "name_city_state"   — multiple name matches, disambiguated by address
            "name_first_pick"   — multiple name matches, none had matching address
                                   (returned the lowest id, logged a warning)
            "created"           — no match found, new donor row inserted
    """
    # ── 1. Exact donor_key match ──
    dk = individual_donor_key(name, city, state)
    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (dk,))
    row = cur.fetchone()
    if row:
        return row[0], "donor_key_exact"

    # ── 2. Exact name match (no nickname fuzz — leaders.csv aligns to FEC) ──
    cur.execute(
        """
        SELECT donor_id FROM donors
        WHERE entity_type = 'INDIVIDUAL'
          AND UPPER(first_name) = %s
          AND UPPER(last_name)  = %s
        """,
        ((first or "").strip().upper(), (last or "").strip().upper()),
    )
    matches = [r[0] for r in cur.fetchall()]

    if len(matches) == 1:
        return matches[0], "name_unique"

    if len(matches) > 1:
        # Disambiguate by city/state. city/state_code live on the shared
        # `addresses` table; `donor_addresses` is just the donor→address link,
        # so join through it to `addresses` (it has no city column of its own).
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
        disambiguated = [r[0] for r in cur.fetchall()]
        if len(disambiguated) == 1:
            return disambiguated[0], "name_city_state"
        if len(disambiguated) > 1:
            logger.warning(
                "Leader %r matches %d donors in %s, %s — picking lowest id",
                name, len(disambiguated), city, state,
            )
            return min(disambiguated), "name_city_state"
        # Name matches exist but in unrelated cities — likely different people.
        # Pick the lowest id and log a warning so the operator can investigate.
        logger.warning(
            "Leader %r has %d name matches but none in %s, %s — picking lowest id",
            name, len(matches), city, state,
        )
        return min(matches), "name_first_pick"

    # ── 3. Create new donor (the 7 leaders never in FEC) ──
    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (
            dk,
            (first or "").strip().upper() or None,
            (last or "").strip().upper() or None,
        ),
    )
    new_id = cur.fetchone()[0]
    logger.info("Created donor #%d (donor_key=%s) for leader %r", new_id, dk, name)
    return new_id, "created"


def upsert_donor_address(
    cur: Any,
    donor_id: int,
    street_1: str | None,
    street_2: str | None,
    city: str | None,
    state: str | None,
    zip_5: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> None:
    """Link a donor to a (shared) address, creating both if needed.

    The physical address lives in the shared `addresses` table (one source of
    truth). This finds an existing matching address row (or inserts one), then
    inserts the donor↔address link if the donor isn't already linked to it.
    Empty strings are normalized to NULL so a partial leader address (city-only)
    doesn't duplicate when an FEC filing later supplies a street.
    IS NOT DISTINCT FROM treats NULL == NULL, matching the loader's dimension dedup.
    """
    def _none_if_empty(s: str | None) -> str | None:
        return s.strip() if s and s.strip() else None

    s1, s2 = _none_if_empty(street_1), _none_if_empty(street_2)
    ci, st, z5 = _none_if_empty(city), _none_if_empty(state), _none_if_empty(zip_5)
    if s1 is None and s2 is None and ci is None and st is None and z5 is None:
        return  # nothing to store

    # 1. Find the shared address row (match the full tuple), else insert it.
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
        (s1, s2, ci, st, z5),
    )
    row = cur.fetchone()
    if row:
        address_id = row[0]
        # Backfill coords if the shared row never got geocoded but we have them.
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
            (s1, s2, ci, st, z5, latitude, longitude),
        )
        address_id = cur.fetchone()[0]

    # 2. Link the donor to that address (dedup the link).
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
    """Give a leader an employment row from their CSV employer.

    Leaders who never donated to FEC have no donor_employments row, so the
    dashboard would show "—" for their role even though leaders.csv lists
    an employer. This creates that row, matching the same shape the
    contribution loader uses:
      • a real company  → look up / insert into `employers`, store employer_id
      • a status word (RETIRED / NOT EMPLOYED / SELF-EMPLOYED) → employer_id
        NULL + the matching employer_status, exactly like a donor's record

    Skips donors who already have an employment row (donated leaders keep
    their FEC-derived employment — the source of truth for their real job).
    No-op when the employer cell is blank.
    """
    emp = (employer or '').strip()
    if not emp:
        return

    # Donated leaders already have employment from FEC — don't override it.
    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    up = emp.upper()
    if up in _NON_EMPLOYER:
        employer_id = None
        status = _STATUS_TO_CODE.get(up, 'not_employed')
    else:
        # Real company: reuse the row if it exists, else create it.
        cur.execute(
            "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (emp,),
        )
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (emp,))
        employer_id = cur.fetchone()[0]
        status = 'active'

    # The guard above already returned if this donor has any employment row,
    # so a plain INSERT is safe — and schema.sql's UNIQUE NULLS NOT DISTINCT
    # (donor_id, employer_id, occupation) on donor_employments backstops it.
    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, employer_status)
        VALUES (%s, %s, NULL, %s)
        """,
        (donor_id, employer_id, status),
    )
