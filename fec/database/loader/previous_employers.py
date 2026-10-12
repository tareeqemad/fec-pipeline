"""Each retired filing's own previous employer."""
from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.cleaning.employer_status import is_real_employer
from fec.log import get_logger

logger = get_logger(__name__)

# not a company; stored as a flag
PREVIOUS_SELF_EMPLOYED = "SELF-EMPLOYED"


# check whether a previous_employer value means self-employed
def _is_previous_self_employed(name) -> bool:
    return not pd.isna(name) and str(name).strip().upper() == PREVIOUS_SELF_EMPLOYED


# retired filing's (company id, self-employed flag)
def filing_previous(status, name, get_employer_id) -> tuple[int | None, bool]:
    if status != "retired" or pd.isna(name) or not str(name).strip():
        return None, False
    if _is_previous_self_employed(name):
        return None, True
    return get_employer_id(str(name).strip()), False


# insert previous companies of retired filings
def link_previous_employers(conn: Any, cur: Any, df: pd.DataFrame, emp_name_to_id: dict) -> None:
    logger.info("\n-- 3/8 Linking previous employers --")
    if 'previous_employer' not in df.columns:
        return
    retired = df['employer_status'].eq('retired') & df['previous_employer'].notna()
    names = {str(name).strip() for name in df.loc[retired, 'previous_employer']} - {""}

    dropped = {name for name in names if not is_real_employer(name) and not _is_previous_self_employed(name)}
    if dropped:
        # SELF-EMPLOYED is a flag; others break contract
        logger.warning(
            "  previous_employer: %d non-company value(s) not stored: %s",
            len(dropped), ", ".join(repr(name) for name in sorted(dropped)[:10]),
        )
    new_names = {name for name in names if is_real_employer(name) and name not in emp_name_to_id}
    if new_names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(name,) for name in sorted(new_names)], page_size=1000)
        cur.execute("SELECT employer_id, name FROM employers")
        emp_name_to_id.clear()
        emp_name_to_id.update({row[1]: row[0] for row in cur.fetchall()})
    logger.info(f"  previous_employer: {int(retired.sum()):,} retired filings name one")
