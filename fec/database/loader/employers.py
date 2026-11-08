"""Load companies and donor employments."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.status import current_employer_name, is_real_employer, referenced_employers
from fec.config.not_employers import NOT_REAL_EMPLOYER
from fec.database.loader._base import to_native
from fec.database.loader.addresses import DONOR_ADDRESS_COLUMNS
from fec.database.loader.locations import employment_address_id, location_index
from fec.log import get_logger

logger = get_logger(__name__)

# Match the seven-field UNIQUE NULLS NOT DISTINCT key in schema.sql.
EMPLOYMENT_KEY_COLUMNS = (
    "donor_id", "employer_id", "occupation", "employer_status",
    "previous_employer_id", "previous_self_employed", "occupation_category_id",
)

# A retired filing's previous self-employment is a flag, never a company.
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"


# Build the seven-field employment key with None for missing values.
def employment_key(donor_id, employer_id, occupation, employer_status,
                   previous_employer_id=None, previous_self_employed=False,
                   occupation_category_id=None) -> tuple:
    employer_id = to_native(employer_id)
    previous_employer_id = to_native(previous_employer_id)
    occupation_category_id = to_native(occupation_category_id)
    return (
        int(donor_id),
        None if employer_id is None else int(employer_id),
        to_native(occupation),
        to_native(employer_status),
        None if previous_employer_id is None else int(previous_employer_id),
        bool(previous_self_employed),
        None if occupation_category_id is None else int(occupation_category_id),
    )


# Create an exact company-name lookup over a live ID map.
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


# Insert names and refresh the ID map in place.
def _insert_employers(cur: Any, names, employer_ids: dict) -> None:
    if names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(name,) for name in sorted(names)], page_size=5000)
    cur.execute("SELECT employer_id, name FROM employers")
    employer_ids.update({name: employer_id for employer_id, name in cur.fetchall()})


# Load every company the filings name, current and previous, plus extras.
def load_employers(cur: Any, df: pd.DataFrame, extra: set[str] = frozenset()) -> dict:
    retired = df['employer_status'].eq('retired') & df['previous_employer'].notna()
    previous = {str(name).strip() for name in df.loc[retired, 'previous_employer']} - {""}
    dropped = {name for name in previous if not is_real_employer(name) and name.upper() != PREVIOUS_SELF_EMPLOYED}
    if dropped:
        # Other non-company values are rejected; self-employment uses its flag.
        logger.warning(
            "  previous_employer: %d non-company value(s) not stored: %s",
            len(dropped), ", ".join(repr(name) for name in sorted(dropped)[:10]),
        )
    employer_ids: dict = {}
    _insert_employers(cur, referenced_employers(df) | set(extra), employer_ids)
    return employer_ids


# One filing's employment key; the company must already be loaded.
def filing_employment_key(donor_id, status, employer, occupation, previous_employer,
                          category_id, get_employer_id, donor_key=None) -> tuple:
    employer_name = current_employer_name(status, employer)
    if status == 'active' and not employer_name:
        raise RuntimeError(f"active employment has no employer: donor_key={donor_key}")
    employer_id = get_employer_id(employer_name)
    if employer_name and employer_id is None:
        raise RuntimeError(f"cleaned employer has no exact database match: {employer_name!r}")

    previous = str(to_native(previous_employer) or "").strip()
    previous_employer_id, previous_self_employed = None, False
    if status == "retired" and previous:
        if previous.upper() == PREVIOUS_SELF_EMPLOYED:
            previous_self_employed = True
        else:
            previous_employer_id = get_employer_id(previous)
    return employment_key(
        donor_id, employer_id, occupation, status,
        previous_employer_id, previous_self_employed, category_id,
    )


# The employment key of every individual filing, by row position.
def filing_employment_keys(rows: pd.DataFrame, donor_ids: dict, occupation_category_ids: dict,
                           get_employer_id) -> list[tuple | None]:
    keys = []
    for row in rows.itertuples(index=False):
        donor_id = donor_ids.get(row.donor_key)
        if row.entity_type != 'INDIVIDUAL' or not donor_id:
            keys.append(None)
            continue
        keys.append(filing_employment_key(
            donor_id, to_native(row.employer_status), row.contributor_employer,
            row.contributor_occupation, getattr(row, 'previous_employer', None),
            occupation_category_ids.get(to_native(row.occupation_category)),
            get_employer_id, row.donor_key,
        ))
    return keys


# Load one employment per distinct key, located by its newest filing.
def load_employments(cur: Any, df: pd.DataFrame, donor_ids: dict,
                     occupation_category_ids: dict, get_employer_id, address_ids: dict,
                     employer_locations: list[dict]) -> dict:
    offices = location_index(employer_locations)
    filings = df[df['entity_type'] == 'INDIVIDUAL'].sort_values(
        "contribution_receipt_date", ascending=False,
    )
    filings = filings.assign(employment_key=filing_employment_keys(
        filings, donor_ids, occupation_category_ids, get_employer_id,
    ))
    filings = filings[filings['employment_key'].notna()].drop_duplicates('employment_key', keep='first')

    rows = []
    for row in filings.itertuples(index=False):
        (donor_id, employer_id, occupation, status,
         previous_employer_id, previous_self_employed, category_id) = row.employment_key
        # A retiree's employment sits at the company they left.
        office_of = row.contributor_employer if employer_id else (
            getattr(row, 'previous_employer', None) if status == 'retired' else None
        )
        address_id = employment_address_id(
            status, office_of, tuple(getattr(row, column) for column in DONOR_ADDRESS_COLUMNS),
            row.contributor_zip, row.contributor_state, row.contributor_city,
            offices, address_ids, row.donor_key,
        )
        rows.append((donor_id, employer_id, occupation, category_id, status,
                     previous_employer_id, previous_self_employed, address_id))
    return insert_employments(cur, rows)


# Insert employment rows and return the key of every stored employment.
def insert_employments(cur: Any, rows: list[tuple]) -> dict:
    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id, previous_self_employed,
            address_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        rows, page_size=5000)
    cur.execute(
        "SELECT donor_employment_id, " + ", ".join(EMPLOYMENT_KEY_COLUMNS)
        + " FROM donor_employments"
    )
    return {employment_key(*row[1:]): row[0] for row in cur.fetchall()}
