"""Classify employers as active, retired, self-employed or not working."""
from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import (
    ADMIN_NOTE_EMPLOYER_RE,
    EMPLOYER_STATUS_VALUES,
    JUNK_EMPLOYER_RE,
)
from fec.config.not_employers import (
    JOB_TITLE_HEADS,
    JOB_TITLE_MODIFIERS,
    NOT_REAL_EMPLOYER,
)

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})


_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


# uppercased, stripped text for a status field; '' if missing
def _status_text(value) -> str:
    return "" if pd.isna(value) else str(value).strip().upper()


# a run of one letter, typed twice or more
_DOUBLED_LETTER_RE = re.compile(r"([A-Z])\1+")


# Doubled letters typed once: 'NNONE' -> 'NONE'.
def single_letters(text: str) -> str:
    return _DOUBLED_LETTER_RE.sub(r"\1", text)


# A doubled-letter status is still the status; short words exact.
_STATUS_SPELLINGS = frozenset(
    single_letters(value) for value in EMPLOYER_STATUS_VALUES if len(value) >= 4
)
# Scrambled status words of six+ letters: 'RETRIED'.
_STATUS_LETTERS = frozenset(
    "".join(sorted(value)) for value in EMPLOYER_STATUS_VALUES
    if len(value) >= 6 and value.isalpha()
)


# A nine+ letter status with one wrong letter.
_LONG_STATUSES = tuple(
    re.sub(r"[^A-Z]", "", value) for value in EMPLOYER_STATUS_VALUES
    if len(re.sub(r"[^A-Z]", "", value)) >= 9
)


# True when one edit from a long status word.
def _long_status_typo(upper: str) -> bool:
    letters = re.sub(r"[^A-Z]", "", upper)
    return len(letters) >= 9 and any(
        abs(len(letters) - len(status)) <= 1 and levenshtein(letters, status) == 1
        for status in _LONG_STATUSES
    )


# Accept title words and the permitted one-letter spelling errors.
def _title_word(word: str, last: bool) -> bool:
    if word in JOB_TITLE_HEADS or (not last and word in JOB_TITLE_MODIFIERS):
        return True
    if not last or len(word) < 6:
        return False
    for head in JOB_TITLE_HEADS:
        difference = abs(len(head) - len(word))
        if difference == 0 or (len(head) >= 8 and difference == 1 and word != head + 'S'):
            if levenshtein(word, head) == 1:
                return True
    return False


def is_job_title(upper: str) -> bool:
    """All title words, the last one naming a job."""
    words = re.findall(r"[A-Z0-9&]+", upper)
    return bool(words) and all(
        _title_word(word, index == len(words) - 1) for index, word in enumerate(words)
    )


_ADMIN_NOTE_RE = re.compile(ADMIN_NOTE_EMPLOYER_RE)
_STRUCTURAL_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
_EMAIL_RE = re.compile(r'[^\s@]+@[^\s@]+\.[^\s@]+')


# Reject known non-company values; accepted names are not verified.
def employer_rejection_reason(emp) -> str:
    if pd.isna(emp):
        return 'missing_value'
    text = re.sub(r'\s+', ' ', str(emp)).strip()
    return _employer_rejection_reason(text.upper())


@lru_cache(maxsize=32768)
def _employer_rejection_reason(upper: str) -> str:
    """Evaluate each distinct normalized name once during a pipeline run."""
    if upper in ('NAN', 'NONE', 'N/A', 'NA', ''):
        return 'missing_value'
    # Ignore terminal punctuation on statuses/admin notes, not punctuation inside names.
    canonical = re.sub(r'\s*;\s*', '; ', upper).rstrip(' .;,:!?')
    # RET is ambiguous: no company, and no invented retirement.
    if canonical == 'RET':
        return 'ambiguous_placeholder'
    # One character or a form placeholder names no company.
    letters = re.sub(r"[^A-Z0-9]", "", upper)
    if len(letters) < 2 or letters == "BLANK":
        return 'structural_junk'
    if canonical in NOT_REAL_EMPLOYER:
        return 'non_company_value'
    if _ADMIN_NOTE_RE.fullmatch(canonical):
        return 'administrative_note'
    if _STRUCTURAL_JUNK_RE.fullmatch(upper) or _EMAIL_RE.fullmatch(upper):
        return 'structural_junk'
    if is_job_title(canonical):
        return 'job_title'
    if (single_letters(canonical) in _STATUS_SPELLINGS
            or "".join(sorted(canonical)) in _STATUS_LETTERS
            or _long_status_typo(canonical)):
        return 'status_typo'
    return ''


def is_real_employer(emp) -> bool:
    """Whether a value can be an employer name."""
    return not employer_rejection_reason(emp)


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
    """A real current company, only when the status permits one."""
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


# every company name the final database must contain
def referenced_employers(df: pd.DataFrame) -> set[str]:
    """Companies the final database must contain."""
    rows = df[df["entity_type"].eq("INDIVIDUAL")]

    names: set[str] = set()
    for status, employer in rows[["employer_status", "contributor_employer"]].itertuples(index=False):
        current = current_employer_name(status, employer)
        if current:
            names.add(current)

    retired = rows[rows["employer_status"].eq("retired")]["previous_employer"]
    names.update(str(name).strip() for name in retired if is_real_employer(name))
    return names
