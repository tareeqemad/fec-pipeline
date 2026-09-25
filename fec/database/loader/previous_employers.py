"""Link each donor employment to the employer the donor left."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.employer_status import is_real_employer
from fec.log import get_logger

logger = get_logger(__name__)

# The previous_employer contract (fec/cleaning/previous_employer.py) keeps this
# literal because it is real information, but it is not a company: it gets no
# employers row and is stored as donor_employments.previous_self_employed.
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"


def _previous_employer_id(status, donor_key, employer_ids):
    """Previous companies belong to retired rows."""
    if status != "retired":
        return None
    return employer_ids.get(donor_key)


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
