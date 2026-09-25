"""Load employers and donor employments."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.employer_status import (
    current_employer_name,
    referenced_employers,
)
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.database.loader.employment_locations import (
    _employment_address_id,
    _location_employer,
    _location_index,
)
from fec.database.loader.previous_employers import (
    _previous_employer_id,
    _previous_self_employed,
)
from fec.log import get_logger

from ._base import _count, to_native

logger = get_logger(__name__)


# One donor_employments row per filing identity.  employer_status is part of
# it because RETIRED / SELF-EMPLOYED / NOT EMPLOYED / blank all resolve to
# employer_id NULL: without the status a donor's SELF-EMPLOYED ATTORNEY and
# RETIRED ATTORNEY filings would share one row and one status.  Must match the
# UNIQUE NULLS NOT DISTINCT constraint in schema.sql.
EMPLOYMENT_KEY_COLUMNS = ("donor_id", "employer_id", "occupation", "employer_status")


# build the dedup identity tuple for one employment row
def employment_key(donor_id, employer_id, occupation, employer_status) -> tuple:
    """The identity of an employment; NaN becomes None so NULL equals NULL, like the constraint."""
    employer_id = to_native(employer_id)
    return (
        int(donor_id),
        None if employer_id is None else int(employer_id),
        to_native(occupation),
        to_native(employer_status),
    )


# keep only the latest filing per raw employment and status
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


# build a resolver mapping cleaned employer name to id
def _make_employer_resolver(emp_name_to_id: dict):
    """Map a cleaned employer name exactly."""
    # look up one employer name's database id, or None
    def _get_employer_id(emp_name):
        if pd.isna(emp_name):
            return None
        emp = str(emp_name).strip()
        if emp.upper() in NOT_REAL_EMPLOYER:
            return None
        return emp_name_to_id.get(emp)

    return _get_employer_id


# insert unique individual employers and return name-to-id mapping
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


# bulk insert donor_employments rows, skipping duplicate keys
def _insert_employments(conn: Any, cur: Any, rows: list[tuple]) -> None:
    """Insert donor_employments rows; a repeated key is skipped."""
    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, previous_self_employed,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows, page_size=5000)
    conn.commit()


# load donor_employments rows and return their key-to-id mapping
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
    rows = _employment_rows(
        df, donor_key_to_id, occ_cat_map, donor_prev_employer_id, get_employer_id,
        addr_dim_id, employer_locations, previous_self_employed,
    )
    _insert_employments(conn, cur, rows)
    empl_donor_emp_to_id = _employment_ids(cur)
    logger.info(f"  donor_employments: {_count(cur, 'donor_employments'):,} ({time.time()-start:.1f}s)")
    return empl_donor_emp_to_id


# build one row per distinct employment from latest filings
def _employment_rows(df: pd.DataFrame, donor_key_to_id: dict, occ_cat_map: dict,
                     donor_prev_employer_id: dict, get_employer_id,
                     addr_dim_id: dict | None, employer_locations: list[dict] | None,
                     previous_self_employed: set[str] | None) -> list[tuple]:
    """One donor_employments row per distinct employment_key, from each donor's latest filings."""
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

        location_address_id = _employment_address_id(
            row,
            _location_employer(row, emp_id, employer_name, emp_status),
            locations,
            addr_dim_id or {},
        )

        empl_rows.append((
            donor_id, emp_id, occ, occ_cat_id,
            emp_status, prev_emp_id, prev_self_employed, location_address_id,
        ))

    return empl_rows


# resolve a filing's current employer name and database id
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


# resolve a retiree's previous employer id or self-employed flag
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


# map each loaded employment's key to its database id
def _employment_ids(cur) -> dict:
    """employment_key(...) -> donor_employment_id for every loaded row."""
    cur.execute(
        "SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS)
        + " FROM donor_employments"
    )
    return {employment_key(*row[1:]): row[0] for row in cur.fetchall()}
