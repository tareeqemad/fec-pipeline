"""Load employers and donor employments."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.config.constants import NOT_REAL_EMPLOYER
from fec.cleaning.previous_employer import (
    current_employer_name,
    is_real_employer,
    referenced_employers,
)
from fec.log import get_logger
from fec.resolve.pipeline.locations import select_location

from ._base import _count, to_native
from .addresses import load_employer_locations, _akey

logger = get_logger(__name__)

# The previous_employer contract (fec/cleaning/previous_employer.py) keeps this
# literal because it is real information, but it is not a company: it gets no
# employers row and is stored as donor_employments.previous_self_employed.
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"

# One donor_employments row per filing identity.  employer_status is part of
# it because RETIRED / SELF-EMPLOYED / NOT EMPLOYED / blank all resolve to
# employer_id NULL: without the status a donor's SELF-EMPLOYED ATTORNEY and
# RETIRED ATTORNEY filings would share one row and one status.  Must match the
# UNIQUE NULLS NOT DISTINCT constraint in schema.sql.
EMPLOYMENT_KEY_COLUMNS = ("donor_id", "employer_id", "occupation", "employer_status")


def employment_key(donor_id, employer_id, occupation, employer_status) -> tuple:
    """The identity of an employment; NaN becomes None so NULL equals NULL, like the constraint."""
    employer_id = to_native(employer_id)
    return (
        int(donor_id),
        None if employer_id is None else int(employer_id),
        to_native(occupation),
        to_native(employer_status),
    )


def _location_index(
    employer_locations: list[dict] | None = None,
) -> dict[str, dict]:
    """Index known locations by exact employer name."""
    grouped: dict[str, list[dict]] = {}
    locations = employer_locations
    if locations is None:
        locations = load_employer_locations()
    for location in locations:
        grouped.setdefault(location["employer_name"], []).append(location)

    lookup = {}
    for name, locations in grouped.items():
        primary = next(
            (location for location in locations if location["is_primary"]),
            None,
        )
        entry = {**(primary or {}), "locations": [
            location for location in locations if location is not primary
        ]}
        lookup[name] = entry
    return lookup


def _employment_address_id(
    row,
    employer_name,
    locations: dict,
    address_ids: dict,
):
    """Choose the address attached to one employment."""
    status = str(to_native(row.get("employer_status")) or "")

    if status == "not_employed":
        return None

    if status == "self_employed":
        address = _akey(
            row.get("contributor_street_1"),
            row.get("contributor_street_2"),
            row.get("contributor_city"),
            row.get("contributor_state"),
            row.get("contributor_zip"),
        )
        if not any(address):
            return None
        address_id = address_ids.get(address)
        if address_id is None:
            raise RuntimeError(
                "self-employed address was not loaded: "
                f"donor_key={row.get('donor_key')}"
            )
        return address_id

    name = str(to_native(employer_name) or "").strip()
    if not name or not locations:
        return None

    entry = locations.get(name)
    location = select_location(
        entry,
        str(to_native(row.get("contributor_zip")) or ""),
        str(to_native(row.get("contributor_state")) or ""),
    )
    if not location:
        return None

    address = _akey(
        location["employer_address"],
        None,
        location["employer_city"],
        location["employer_state"],
        location["employer_zip"],
    )
    address_id = address_ids.get(address)
    if address_id is None:
        raise RuntimeError(f"employer address was not loaded: {name!r}")
    return address_id


def _latest_employment_rows(individuals: pd.DataFrame) -> pd.DataFrame:
    """Keep the latest complete filing per raw employment and status."""
    keys = [
        "donor_key",
        "contributor_employer",
        "contributor_occupation",
    ]
    # A status is part of the employment identity, so a filing whose status
    # differs from a newer one with the same raw text keeps its own candidate.
    if "employer_status" in individuals.columns:
        keys.append("employer_status")
    return (
        individuals.sort_values(
            "contribution_receipt_date",
            ascending=False,
        )
        .drop_duplicates(keys, keep="first")
    )


def _previous_employer_id(status, donor_key, employer_ids):
    """Previous companies belong to retired rows."""
    if status != "retired":
        return None
    return employer_ids.get(donor_key)


def _make_employer_resolver(emp_name_to_id: dict):
    """Map a cleaned employer name exactly."""

    def _get_employer_id(emp_name):
        if pd.isna(emp_name):
            return None
        emp = str(emp_name).strip()
        if emp.upper() in NOT_REAL_EMPLOYER:
            return None
        return emp_name_to_id.get(emp)

    return _get_employer_id


def load_employers(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 2: unique employers of INDIVIDUAL donors; returns name -> employer_id."""
    logger.info("\n-- 2/8 Loading employers --")
    start = time.time()

    employer_rows = [(name,) for name in sorted(referenced_employers(df))]

    execute_values(cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows, page_size=5000)
    conn.commit()

    cur.execute("SELECT employer_id, name FROM employers")
    emp_name_to_id = {row[1]: row[0] for row in cur.fetchall()}
    logger.info(f"  employers: {_count(cur, 'employers'):,} ({time.time()-start:.1f}s)")
    return emp_name_to_id


def _latest_previous_employers(df: pd.DataFrame) -> pd.DataFrame:
    """Each donor's newest retired filing that names a previous employer."""
    if 'previous_employer' not in df.columns:
        return df.iloc[0:0]
    prev_emp = df[
        df['previous_employer'].notna()
        & df['employer_status'].eq('retired')
    ]
    return (prev_emp.sort_values('contribution_receipt_date', ascending=False)
            .drop_duplicates('donor_key', keep='first'))


def _is_previous_self_employed(name) -> bool:
    return not pd.isna(name) and str(name).strip().upper() == PREVIOUS_SELF_EMPLOYED


def previous_self_employed_donors(df: pd.DataFrame) -> set[str]:
    """Donors whose newest previous employer is the contract's 'SELF-EMPLOYED'.

    Same donor-level selection as link_previous_employers, so a donor gets
    either a previous company or this flag, never both.
    """
    latest = _latest_previous_employers(df)
    if len(latest) == 0:
        return set()
    flagged = latest['previous_employer'].map(_is_previous_self_employed)
    donors = set(latest.loc[flagged, 'donor_key'])
    if donors:
        logger.info(f"  previous_employer: {len(donors):,} donors previously self-employed")
    return donors


def _previous_self_employed(status, donor_key, donors: set[str]) -> bool:
    """Like previous_employer_id, the flag belongs only to retired rows."""
    return status == "retired" and donor_key in donors


def link_previous_employers(conn: Any, cur: Any, df: pd.DataFrame,
                            emp_name_to_id: dict) -> dict[str, int]:
    """Step 3: link exact previous-employer names."""
    logger.info("\n-- 3/8 Linking previous employers --")

    donor_prev_employer_id: dict[str, int] = {}
    prev_latest = _latest_previous_employers(df)
    if len(prev_latest) == 0:
        return donor_prev_employer_id

    prev_names = set()
    dropped: set[str] = set()
    for name in prev_latest['previous_employer']:
        employer_name = str(name).strip()
        if not is_real_employer(employer_name):
            # SELF-EMPLOYED is stored as previous_self_employed; anything
            # else breaks the previous_employer contract and is not stored.
            if not _is_previous_self_employed(employer_name):
                dropped.add(employer_name)
            continue
        if employer_name not in emp_name_to_id:
            prev_names.add(employer_name)
    if dropped:
        logger.warning(
            "  previous_employer: %d non-company value(s) not stored: %s",
            len(dropped), ", ".join(repr(name) for name in sorted(dropped)[:10]),
        )
    if prev_names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(prev_name,) for prev_name in prev_names], page_size=1000)
        conn.commit()
        cur.execute("SELECT employer_id, name FROM employers")
        emp_name_to_id.clear()
        emp_name_to_id.update({row[1]: row[0] for row in cur.fetchall()})

    for _, row in prev_latest.iterrows():
        donor_key = row['donor_key']
        employer_name = str(row['previous_employer']).strip()
        emp_id = emp_name_to_id.get(employer_name)
        if donor_key and emp_id:
            donor_prev_employer_id[donor_key] = emp_id
    if donor_prev_employer_id:
        logger.info(f"  previous_employer: {len(donor_prev_employer_id):,} donors mapped")
    return donor_prev_employer_id


def load_employments(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                     occ_cat_map: dict, donor_prev_employer_id: dict,
                     get_employer_id, addr_dim_id: dict | None = None,
                     employer_locations: list[dict] | None = None,
                     previous_self_employed: set[str] | None = None) -> dict:
    """Step 6: donor_employments rows; returns employment_key(...) -> donor_employment_id.

    The key is (donor_id, employer_id, occupation, employer_status), so every
    filing keeps the status it reported (and a retired filing its previous
    employer) even when several statuses share employer_id NULL.
    """
    logger.info("\n-- 6/8 Loading donor employments --")
    start = time.time()

    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    empl_rows = []
    seen_empl = set()

    locations = (
        _location_index(employer_locations)
        if addr_dim_id else {}
    )
    empl_agg = _latest_employment_rows(individuals)

    for _, row in empl_agg.iterrows():
        donor_key = row['donor_key']
        emp_val = row['contributor_employer']
        occ = to_native(row['contributor_occupation'])

        donor_id = donor_key_to_id.get(donor_key)
        if not donor_id:
            continue

        emp_status = to_native(row.get('employer_status'))
        employer_name, emp_id = _current_employer(
            emp_status, emp_val, donor_key, get_employer_id,
        )

        dedup_key = employment_key(donor_id, emp_id, occ, emp_status)
        if dedup_key in seen_empl:
            continue
        seen_empl.add(dedup_key)

        occ_cat = to_native(row.get('occupation_category'))
        occ_cat_id = occ_cat_map.get(occ_cat)

        prev_emp_id, prev_self_employed = _previous_employment(
            emp_status, donor_key, donor_prev_employer_id, previous_self_employed or set(),
        )

        if emp_id:
            location_employer = employer_name
        elif emp_status == 'retired':
            location_employer = row.get('previous_employer')
        else:
            location_employer = None

        location_address_id = _employment_address_id(
            row,
            location_employer,
            locations,
            addr_dim_id or {},
        )

        empl_rows.append((
            donor_id, emp_id, occ, occ_cat_id,
            emp_status, prev_emp_id, prev_self_employed, location_address_id,
        ))

    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, previous_self_employed,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        empl_rows, page_size=5000)
    conn.commit()

    empl_donor_emp_to_id = _employment_ids(cur)
    logger.info(f"  donor_employments: {_count(cur, 'donor_employments'):,} ({time.time()-start:.1f}s)")
    return empl_donor_emp_to_id


def _current_employer(emp_status, emp_val, donor_key, get_employer_id):
    """The filing's current employer name and its database id."""
    employer_name = current_employer_name(emp_status, emp_val)
    if emp_status == 'active' and not employer_name:
        raise RuntimeError(
            f"active employment has no employer: donor_key={donor_key}"
        )
    emp_id = get_employer_id(employer_name)
    if employer_name and emp_id is None:
        raise RuntimeError(
            f"cleaned employer has no exact database match: {employer_name!r}"
        )
    return employer_name, emp_id


def _previous_employment(emp_status, donor_key, donor_prev_employer_id, previous_self_employed):
    """A retiree's previous employer id or SELF-EMPLOYED flag, never both."""
    prev_emp_id = _previous_employer_id(
        emp_status,
        donor_key,
        donor_prev_employer_id,
    )
    prev_self_employed = _previous_self_employed(
        emp_status,
        donor_key,
        previous_self_employed,
    )
    if prev_self_employed and prev_emp_id is not None:
        raise RuntimeError(
            "previous employer is both a company and SELF-EMPLOYED: "
            f"donor_key={donor_key}"
        )
    return prev_emp_id, prev_self_employed


def _employment_ids(cur) -> dict:
    """employment_key(...) -> donor_employment_id for every loaded row."""
    cur.execute(
        "SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS)
        + " FROM donor_employments"
    )
    return {employment_key(*row[1:]): row[0] for row in cur.fetchall()}
