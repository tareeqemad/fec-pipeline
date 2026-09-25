"""Classify each filing's employer as active, retired, self-employed or not working."""
from __future__ import annotations

import pandas as pd

from fec.config.constants import (
    NOT_REAL_EMPLOYER,
)

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})


_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


def _status_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip().upper()


def is_real_employer(emp) -> bool:
    """True if emp names a real company (not RETIRED, SELF-EMPLOYED, junk)."""
    if pd.isna(emp):
        return False
    text = str(emp).strip()
    if text.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    return text.upper() not in NOT_REAL_EMPLOYER


def classify_employer_status(employer, occupation="", category="") -> str:
    """Classify work status without changing the reported company."""
    emp = _status_text(employer)
    occ = _status_text(occupation)
    cat = _status_text(category)

    if emp == "RETIRED":
        return "retired"
    no_work = (
        emp in _NO_WORK_OCCUPATIONS
        or cat in _NO_WORK_CATEGORIES
        or occ in _NO_WORK_OCCUPATIONS
    )
    if no_work:
        return "not_employed"
    if emp in {"SELF-EMPLOYED", "SELF EMPLOYED"} or cat == "SELF-EMPLOYED":
        return "self_employed"
    return "active" if is_real_employer(employer) else "missing"


def classify_employer_statuses(df: pd.DataFrame) -> pd.Series:
    """Classify every row in a frame."""
    blank = pd.Series("", index=df.index)
    employers = df.get("contributor_employer", blank)
    occupations = df.get("contributor_occupation", blank)
    categories = df.get("occupation_category", blank)
    return pd.Series([
        classify_employer_status(employer, occupation, category)
        for employer, occupation, category in zip(
            employers, occupations, categories,
        )
    ], index=df.index)


def current_employer_name(status, employer) -> str:
    """Return a real current company only when the status permits one."""
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


def referenced_employers(df: pd.DataFrame) -> set[str]:
    """Companies the final database must contain."""
    rows = df
    if "entity_type" in rows.columns:
        rows = rows[rows["entity_type"].eq("INDIVIDUAL")]

    names: set[str] = set()
    for status, employer in rows[["employer_status", "contributor_employer"]].itertuples(index=False):
        current = current_employer_name(status, employer)
        if current:
            names.add(current)

    retired = rows[rows["employer_status"].eq("retired")]["previous_employer"]
    names.update(str(name).strip() for name in retired if is_real_employer(name))
    return names
