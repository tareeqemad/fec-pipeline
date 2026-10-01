"""Classify each filing's employer as active, retired, self-employed or not working."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.not_employers import NOT_REAL_EMPLOYER

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})


_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


# uppercased, stripped text for a status field; '' if missing
def _status_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip().upper()


# a run of one letter, typed twice or more
_DOUBLED_LETTER_RE = re.compile(r"([A-Z])\1+")


# the text with every doubled letter typed once ('NNONE' -> 'NONE')
def single_letters(text: str) -> str:
    return _DOUBLED_LETTER_RE.sub(r"\1", text)


# a status word typed with a doubled letter is still the status, never a company;
# short ones stay exact (NAAN is a bakery, not NAN)
_STATUS_SPELLINGS = frozenset(
    single_letters(value) for value in EMPLOYER_STATUS_VALUES if len(value) >= 4
)


# true if emp names a real company, not junk
def is_real_employer(emp) -> bool:
    """True if emp names a real company (not RETIRED, SELF-EMPLOYED, junk)."""
    if pd.isna(emp):
        return False
    text = str(emp).strip()
    if text.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    upper = text.upper()
    return upper not in NOT_REAL_EMPLOYER and single_letters(upper) not in _STATUS_SPELLINGS


# classify one filing's work status without changing the reported company
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


# classify_employer_status applied across every row of a frame
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


# the real current company name, only when status allows
def current_employer_name(status, employer) -> str:
    """Return a real current company only when the status permits one."""
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


# every company name the final database must contain
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
