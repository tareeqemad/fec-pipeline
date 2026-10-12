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
from fec.database.loader._base import count_rows, to_native
from fec.database.loader.employment_locations import (
    employment_address_id,
    location_employer,
    location_index,
)
from fec.database.loader.previous_employers import filing_previous
from fec.log import get_logger

logger = get_logger(__name__)


# must match UNIQUE constraint in schema.sql
EMPLOYMENT_KEY_COLUMNS = (
    "donor_id", "employer_id", "occupation", "employer_status",
    "previous_employer_id", "previous_self_employed",
)


# employment identity; NaN to None like constraint
def employment_key(donor_id, employer_id, occupation, employer_status,
                   previous_employer_id=None, previous_self_employed=False) -> tuple:
    employer_id = to_native(employer_id)
    previous_employer_id = to_native(previous_employer_id)
    return (
        int(donor_id),
        None if employer_id is None else int(employer_id),
        to_native(occupation),
        to_native(employer_status),
        None if previous_employer_id is None else int(previous_employer_id),
        bool(previous_self_employed),
    )


# latest filing per raw employment and status
def _latest_employment_rows(individuals: pd.DataFrame) -> pd.DataFrame:
    keys = [
        "donor_key",
        "contributor_employer",
        "contributor_occupation",
    ]
    # status is part of employment identity
    if "employer_status" in individuals.columns:
        keys.append("employer_status")
    # so is the previous employer
    if "previous_employer" in individuals.columns:
        keys.append("previous_employer")
    return (
        individuals.sort_values(
            "contribution_receipt_date",
            ascending=False,
        )
        .drop_duplicates(keys, keep="first")
    )


# resolve cleaned employer names to ids exactly
def make_employer_resolver(emp_name_to_id: dict):
    # one employer name's id, or None
    def _get_employer_id(emp_name):
        if pd.isna(emp_name):
            return None
        emp = str(emp_name).strip()
        if emp.upper() in NOT_REAL_EMPLOYER:
            return None
        return emp_name_to_id.get(emp)

    return _get_employer_id


# insert INDIVIDUAL employers; return name-to-id map
def load_employers(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    logger.info("\n-- 2/8 Loading employers --")
    start = time.time()

    employer_rows = [(name,) for name in sorted(referenced_employers(df))]

    execute_values(cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows, page_size=5000)

    cur.execute("SELECT employer_id, name FROM employers")
    emp_name_to_id = {row[1]: row[0] for row in cur.fetchall()}
    logger.info(f"  employers: {count_rows(cur, 'employers'):,} ({time.time()-start:.1f}s)")
    return emp_name_to_id


# bulk insert donor_employments rows, skipping duplicate keys
def _insert_employments(conn: Any, cur: Any, rows: list[tuple]) -> None:
    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, previous_self_employed,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows, page_size=5000)


# load employments; return key-to-id map
def load_employments(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                     occ_cat_map: dict, get_employer_id, addr_dim_id: dict | None = None,
                     employer_locations: list[dict] | None = None) -> dict:
    logger.info("\n-- 6/8 Loading donor employments --")
    start = time.time()
    rows = _employment_rows(
        df, donor_key_to_id, occ_cat_map, get_employer_id, addr_dim_id, employer_locations,
    )
    _insert_employments(conn, cur, rows)
    empl_donor_emp_to_id = _employment_ids(cur)
    logger.info(f"  donor_employments: {count_rows(cur, 'donor_employments'):,} ({time.time()-start:.1f}s)")
    return empl_donor_emp_to_id


# one row per employment key, latest filings
def _employment_rows(df: pd.DataFrame, donor_key_to_id: dict, occ_cat_map: dict,
                     get_employer_id, addr_dim_id: dict | None,
                     employer_locations: list[dict] | None) -> list[tuple]:
    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    empl_rows = []
    seen_empl = set()

    locations = (
        location_index(employer_locations)
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

        prev_emp_id, prev_self_employed = filing_previous(
            emp_status, row.get('previous_employer'), get_employer_id,
        )
        dedup_key = employment_key(donor_id, emp_id, occ, emp_status, prev_emp_id, prev_self_employed)
        if dedup_key in seen_empl:
            continue
        seen_empl.add(dedup_key)

        occ_cat = to_native(row.get('occupation_category'))
        occ_cat_id = occ_cat_map.get(occ_cat)

        location_address_id = employment_address_id(
            row,
            location_employer(row, emp_id, employer_name, emp_status),
            locations,
            addr_dim_id or {},
        )

        empl_rows.append((
            donor_id, emp_id, occ, occ_cat_id,
            emp_status, prev_emp_id, prev_self_employed, location_address_id,
        ))

    return empl_rows


# filing's current employer name and id
def _current_employer(emp_status, emp_val, donor_key, get_employer_id):
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


# map loaded employment keys to ids
def _employment_ids(cur) -> dict:
    cur.execute(
        "SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS)
        + " FROM donor_employments"
    )
    return {employment_key(*row[1:]): row[0] for row in cur.fetchall()}
