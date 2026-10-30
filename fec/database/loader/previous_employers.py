"""Read each retired filing's previous company or self-employment flag."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.work.status import is_real_employer
from fec.log import get_logger

logger = get_logger(__name__)

# Stored as previous_self_employed, never as a company in employers.
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"


# Check the previous self-employment marker.
def _is_previous_self_employed(name) -> bool:
    return not pd.isna(name) and str(name).strip().upper() == PREVIOUS_SELF_EMPLOYED


# Use this retired filing's company or self-employment flag.
def filing_previous(status, name, get_employer_id) -> tuple[int | None, bool]:
    if status != "retired" or pd.isna(name) or not str(name).strip():
        return None, False
    if _is_previous_self_employed(name):
        return None, True
    return get_employer_id(str(name).strip()), False


# Add missing companies named by retired filings.
def link_previous_employers(cur: Any, df: pd.DataFrame, employer_ids: dict) -> None:
    retired = df['employer_status'].eq('retired') & df['previous_employer'].notna()
    names = {str(name).strip() for name in df.loc[retired, 'previous_employer']} - {""}

    valid_names = {name for name in names if is_real_employer(name)}
    dropped = {name for name in names - valid_names if not _is_previous_self_employed(name)}
    if dropped:
        # Other non-company values are rejected; self-employment uses its flag.
        logger.warning(
            "  previous_employer: %d non-company value(s) not stored: %s",
            len(dropped), ", ".join(repr(name) for name in sorted(dropped)[:10]),
        )
    new_names = valid_names - employer_ids.keys()
    if new_names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(name,) for name in sorted(new_names)], page_size=1000)
        cur.execute("SELECT employer_id, name FROM employers")
        employer_ids.clear()
        employer_ids.update({name: employer_id for employer_id, name in cur.fetchall()})
