"""Classify each filing's employer work status."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.not_employers import JOB_TITLE_HEADS, JOB_TITLE_MODIFIERS, NOT_REAL_EMPLOYER

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})


_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


# uppercased stripped status text; '' if missing
def _status_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip().upper()


# one letter typed twice or more
_DOUBLED_LETTER_RE = re.compile(r"([A-Z])\1+")


# collapse doubled letters, 'NNONE' to 'NONE'
def single_letters(text: str) -> str:
    return _DOUBLED_LETTER_RE.sub(r"\1", text)


# doubled-letter status typos; short words stay exact
_STATUS_SPELLINGS = frozenset(
    single_letters(value) for value in EMPLOYER_STATUS_VALUES if len(value) >= 4
)
# scrambled status letters ('RETIERD'), six letters minimum
_STATUS_LETTERS = frozenset(
    "".join(sorted(value)) for value in EMPLOYER_STATUS_VALUES
    if len(value) >= 6 and value.isalpha()
)


# 9+ letters so short companies never match
_LONG_STATUSES = tuple(
    re.sub(r"[^A-Z]", "", value) for value in EMPLOYER_STATUS_VALUES
    if len(re.sub(r"[^A-Z]", "", value)) >= 9
)


# true when one edit from long status
def _long_status_typo(upper: str) -> bool:
    letters = re.sub(r"[^A-Z]", "", upper)
    return len(letters) >= 9 and any(
        abs(len(letters) - len(status)) <= 1 and levenshtein(letters, status) == 1
        for status in _LONG_STATUSES
    )


# true if emp names a real company
def is_real_employer(emp) -> bool:
    if pd.isna(emp):
        return False
    text = str(emp).strip()
    if text.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    upper = text.upper()
    # single characters and BLANK placeholders aren't companies
    letters = re.sub(r"[^A-Z0-9]", "", upper)
    if len(letters) < 2 or letters == "BLANK":
        return False
    return (
        upper not in NOT_REAL_EMPLOYER
        and single_letters(upper) not in _STATUS_SPELLINGS
        and "".join(sorted(upper)) not in _STATUS_LETTERS
        and not _long_status_typo(upper)
    )


# classify work status, keeping the reported company
def classify_employer_status(employer, occupation="", category="") -> str:
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


# classify_employer_status for every row
def classify_employer_statuses(df: pd.DataFrame) -> pd.Series:
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


# real current company, only when status allows
def current_employer_name(status, employer) -> str:
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


# company names the final database must contain
def referenced_employers(df: pd.DataFrame) -> set[str]:
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


# title word, or one-typo job word
def _title_word(word: str, last: bool) -> bool:
    if word in JOB_TITLE_HEADS or (not last and word in JOB_TITLE_MODIFIERS):
        return True
    if not last or len(word) < 6:
        return False
    for head in JOB_TITLE_HEADS:
        if len(head) == len(word) and levenshtein(word, head) == 1:
            return True
        if len(head) >= 8 and word != head + 'S' and abs(len(head) - len(word)) == 1 \
                and levenshtein(word, head) == 1:
            return True
    return False


# true when value is a job title
def is_job_title(upper: str) -> bool:
    words = re.findall(r"[A-Z0-9&]+", upper)
    return bool(words) and all(
        _title_word(word, index == len(words) - 1) for index, word in enumerate(words)
    )
