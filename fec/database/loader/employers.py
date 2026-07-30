"""Employers table: load, employer-name resolution, previous-employer linking, donor_employments."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.cleaning.employer_synonyms import EMPLOYER_SYNONYMS, canonical_key
from fec.log import get_logger

from ._base import NON_EMPLOYER_STATUSES, _count, to_native
from .addresses import load_employer_branches, _akey

logger = get_logger(__name__)


def _build_ck_index(emp_name_to_id: dict) -> dict[str, int]:
    """canonical_key(name) -> employer_id, first name wins per key; the ONE builder for every ck index in the loader."""
    index: dict[str, int] = {}
    for name, employer_id in emp_name_to_id.items():
        key = canonical_key(name)
        if key and key not in index:
            index[key] = employer_id
    return index


def _make_employer_resolver(emp_name_to_id: dict):
    """Build _get_employer_id over the COMPLETE employer map; call after link_previous_employers (the ck index is never refreshed -- safe because no later step inserts employers)."""
    ck_index = _build_ck_index(emp_name_to_id)

    def _get_employer_id(emp_name):
        """Employer id by name (exact -> synonym -> canonical_key); None for status words."""
        if pd.isna(emp_name):
            return None
        emp = str(emp_name).strip()
        if emp.upper() in NON_EMPLOYER_STATUSES:
            return None
        employer_id = emp_name_to_id.get(emp) or emp_name_to_id.get(emp.upper())
        if employer_id:
            return employer_id
        synonym = EMPLOYER_SYNONYMS.get(emp.upper())
        if synonym:
            employer_id = emp_name_to_id.get(synonym) or emp_name_to_id.get(synonym.upper())
            if employer_id:
                return employer_id
        key = canonical_key(synonym or emp)
        return ck_index.get(key) if key else None

    return _get_employer_id


def load_employers(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 2: unique employers of INDIVIDUAL donors; returns name -> employer_id."""
    logger.info("\n-- 2/8 Loading employers --")
    start = time.time()

    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    # contributor_employer arrives already normalized by the cleaning pipeline.
    emp_series = individuals['contributor_employer'].dropna()
    emp_unique = emp_series[~emp_series.isin(NON_EMPLOYER_STATUSES)].unique()

    employer_rows = [(name,) for name in emp_unique]

    execute_values(cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows, page_size=5000)
    conn.commit()

    cur.execute("SELECT employer_id, name FROM employers")
    emp_name_to_id = {row[1]: row[0] for row in cur.fetchall()}
    logger.info(f"  employers: {_count(cur, 'employers'):,} ({time.time()-start:.1f}s)")
    return emp_name_to_id


def link_previous_employers(conn: Any, cur: Any, df: pd.DataFrame,
                            emp_name_to_id: dict) -> dict[str, int]:
    """Step 3: insert previous_employer companies missing from employers; grows emp_name_to_id IN PLACE (must run before _make_employer_resolver); returns donor_key -> previous employer_id."""
    logger.info("\n-- 3/8 Linking previous employers --")

    donor_prev_employer_id: dict[str, int] = {}
    if 'previous_employer' not in df.columns:
        return donor_prev_employer_id
    prev_emp = df[df['previous_employer'].notna() & (df['previous_employer'] != '')]
    if len(prev_emp) == 0:
        return donor_prev_employer_id

    prev_latest = (prev_emp.sort_values('contribution_receipt_date', ascending=False)
                   .drop_duplicates('donor_key', keep='first'))

    # Route via canonical_key so "KIRKLAND & ELLIS" reuses the existing
    # "KIRKLAND & ELLIS LLP" row instead of becoming a second employer.
    ck_to_emp_id = _build_ck_index(emp_name_to_id)

    prev_names = set()
    for name in prev_latest['previous_employer']:
        employer_name = str(name).strip()
        if not employer_name:
            continue
        # Synonym dict first (WHATSAPP -> WHATSAPP LLC, etc.)
        upper_name = employer_name.upper()
        employer_name = EMPLOYER_SYNONYMS.get(upper_name, employer_name)
        if employer_name.upper() in NON_EMPLOYER_STATUSES:
            continue
        key = canonical_key(employer_name)
        if key and key in ck_to_emp_id:
            continue
        if employer_name not in emp_name_to_id and employer_name.upper() not in emp_name_to_id:
            prev_names.add(employer_name)
    if prev_names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(prev_name,) for prev_name in prev_names], page_size=1000)
        conn.commit()
        cur.execute("SELECT employer_id, name FROM employers")
        emp_name_to_id.clear()
        emp_name_to_id.update({row[1]: row[0] for row in cur.fetchall()})
        # Refresh the ck index after the inserts
        ck_to_emp_id = _build_ck_index(emp_name_to_id)

    for _, row in prev_latest.iterrows():
        donor_key = row['donor_key']
        raw = str(row['previous_employer']).strip()
        normalized = EMPLOYER_SYNONYMS.get(raw.upper(), raw)
        emp_id = (emp_name_to_id.get(normalized)
                  or emp_name_to_id.get(normalized.upper())
                  or ck_to_emp_id.get(canonical_key(normalized)))
        if donor_key and emp_id:
            donor_prev_employer_id[donor_key] = emp_id
    if donor_prev_employer_id:
        logger.info(f"  previous_employer: {len(donor_prev_employer_id):,} donors mapped")
    return donor_prev_employer_id


def load_employments(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                     occ_cat_map: dict, donor_prev_employer_id: dict,
                     get_employer_id, addr_dim_id: dict | None = None) -> dict:
    """Step 6: donor_employments rows; returns (donor_id, employer_id, occupation) -> donor_employment_id."""
    logger.info("\n-- 6/8 Loading donor employments --")
    start = time.time()

    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    empl_rows = []
    seen_empl = set()

    # The donor's state picks their branch office; addr_dim_id turns that branch
    # into the shared addresses row. Both absent -> every address_id is NULL and
    # the views fall back to the company HQ.
    branches = load_employer_branches() if addr_dim_id else {}

    # Grouping by (donor, employer, occupation) preserves career progression --
    # it is the schema's UNIQUE key; date ranges come from contributions later.
    agg_specs = {
        'occupation_category': ('occupation_category', 'first'),
        'contributor_state': ('contributor_state', 'first'),
    }
    if 'employer_status' in individuals.columns:
        agg_specs['employer_status'] = ('employer_status', 'first')

    empl_agg = (
        individuals.groupby(['donor_key', 'contributor_employer', 'contributor_occupation'],
                            dropna=False)
        .agg(**agg_specs)
        .reset_index()
    )

    for _, row in empl_agg.iterrows():
        donor_key = row['donor_key']
        emp_val = row['contributor_employer']
        occ = to_native(row['contributor_occupation'])

        donor_id = donor_key_to_id.get(donor_key)
        if not donor_id:
            continue

        emp_id = get_employer_id(emp_val)

        dedup_key = (donor_id, emp_id, occ or '')
        if dedup_key in seen_empl:
            continue
        seen_empl.add(dedup_key)

        occ_cat = to_native(row.get('occupation_category'))
        occ_cat_id = occ_cat_map.get(occ_cat)

        emp_status = to_native(row.get('employer_status')) if 'employer_status' in row.index else None
        prev_emp_id = donor_prev_employer_id.get(donor_key)

        branch_address_id = None
        if branches:
            donor_state = str(to_native(row.get('contributor_state')) or '').upper()
            branch = branches.get((to_native(emp_val), donor_state))
            if branch:
                branch_address_id = addr_dim_id.get(_akey(
                    branch['address'], None, branch['city'], branch['state'], branch['zip']))

        empl_rows.append((
            donor_id, emp_id, occ, occ_cat_id,
            emp_status, prev_emp_id, branch_address_id,
        ))

    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        empl_rows, page_size=5000)
    conn.commit()

    # Keyed by (donor_id, employer_id, occupation): career progression means
    # several occupations per donor+employer.
    cur.execute("SELECT donor_employment_id, donor_id, employer_id, occupation FROM donor_employments")
    empl_donor_emp_to_id = {}
    for row in cur.fetchall():
        empl_donor_emp_to_id[(row[1], row[2], row[3])] = row[0]
    logger.info(f"  donor_employments: {_count(cur, 'donor_employments'):,} ({time.time()-start:.1f}s)")
    return empl_donor_emp_to_id
