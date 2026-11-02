"""Load employers and donor employments."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.status import (
    current_employer_name,
    referenced_employers,
)
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.database.loader._base import to_int_or_none, to_native
from fec.database.loader.employment_locations import (
    _employment_address_id,
    _location_index,
)
from fec.database.loader.previous_employers import filing_previous


# Match the UNIQUE NULLS NOT DISTINCT key in schema.sql.
EMPLOYMENT_KEY_COLUMNS = (
    "donor_id", "employer_id", "occupation", "employer_status",
    "previous_employer_id", "previous_self_employed", "occupation_category_id",
    "filing_work_address_id",
)


# Create an exact company-name lookup.
def _make_employer_resolver(employer_ids: dict):
    # Reject non-company values and find the exact company name.
    def get_employer_id(name):
        if pd.isna(name):
            return None
        employer = str(name).strip()
        if employer.upper() in NOT_REAL_EMPLOYER:
            return None
        return employer_ids.get(employer)

    return get_employer_id


# Load referenced companies and return their IDs.
def load_employers(cur: Any, df: pd.DataFrame) -> dict:
    employer_rows = [(name,) for name in sorted(referenced_employers(df))]

    execute_values(cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows, page_size=5000)

    cur.execute("SELECT employer_id, name FROM employers")
    return {name: employer_id for employer_id, name in cur.fetchall()}


# Load employment rows and return the employment ID for each filing.
def load_employments(cur: Any, df: pd.DataFrame, donor_ids: dict,
                     occupation_category_ids: dict, get_employer_id, address_ids: dict | None = None,
                     employer_locations: list[dict] | None = None) -> dict:
    rows, filing_keys = _employment_rows(
        df, donor_ids, occupation_category_ids, get_employer_id, address_ids, employer_locations,
    )
    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, employer_status,
            previous_employer_id, previous_self_employed, occupation_category_id,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows.values(), page_size=5000)
    columns = ", ".join(EMPLOYMENT_KEY_COLUMNS)
    cur.execute(f"SELECT donor_employment_id, {columns} FROM donor_employments")
    ids_by_key = {tuple(row[1:]): row[0] for row in cur.fetchall()}
    return {sub_id: ids_by_key[key] for sub_id, key in filing_keys.items()}


# Current work uses this filing's address. Previous work keeps the newest inferred office.
def _employment_rows(df: pd.DataFrame, donor_ids: dict, occupation_category_ids: dict,
                     get_employer_id, address_ids: dict | None,
                     employer_locations: list[dict] | None) -> tuple[dict, dict]:
    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    employment_rows = {}
    filing_keys = {}
    address_ids = address_ids or {}
    locations = _location_index(employer_locations) if address_ids else {}

    for _, row in individuals.sort_values('contribution_receipt_date', ascending=False).iterrows():
        donor_key = row['donor_key']
        donor_id = donor_ids.get(donor_key)
        if not donor_id:
            continue

        status = to_native(row.get('employer_status'))
        employer_name, employer_id = _current_employer(
            status, row['contributor_employer'], donor_key, get_employer_id,
        )
        previous_id, previous_self_employed = filing_previous(
            status, row.get('previous_employer'), get_employer_id,
        )
        category_id = occupation_category_ids.get(to_native(row.get('occupation_category')))
        job_fields = (
            int(donor_id), to_int_or_none(employer_id),
            to_native(row['contributor_occupation']), status,
            to_int_or_none(previous_id), previous_self_employed, to_int_or_none(category_id),
        )
        work_address_id = None
        if status in ('active', 'self_employed'):
            work_address_id = _employment_address_id(row, employer_name, locations, address_ids)
        key = (*job_fields, work_address_id)
        filing_keys[row['sub_id']] = key
        if key in employment_rows:
            continue

        if status == 'retired':
            work_address_id = _employment_address_id(row, row.get('previous_employer'), locations, address_ids)
        employment_rows[key] = (*job_fields, work_address_id)

    return employment_rows, filing_keys


# Find the filing company and require its database ID.
def _current_employer(status, employer, donor_key, get_employer_id):
    employer_name = current_employer_name(status, employer)
    if status == 'active' and not employer_name:
        raise RuntimeError(
            f"active employment has no employer: donor_key={donor_key}"
        )
    employer_id = get_employer_id(employer_name)
    if employer_name and employer_id is None:
        raise RuntimeError(
            f"cleaned employer has no exact database match: {employer_name!r}"
        )
    return employer_name, employer_id
