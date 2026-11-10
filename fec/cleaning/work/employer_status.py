"""Classify employers as active, retired, self-employed or not working."""

from __future__ import annotations

import re
from functools import lru_cache

import pandas as pd
from rapidfuzz.distance import Levenshtein

from fec.cleaning.work.status_spellings import status_word
from fec.vocabulary.not_employers import JOB_TITLE_HEADS, JOB_TITLE_MODIFIERS, NOT_REAL_EMPLOYER
from fec.vocabulary.statuses import ADMIN_NOTE_EMPLOYER_RE, EMPLOYER_STATUS_VALUES, JUNK_EMPLOYER_RE

_NO_WORK_CATEGORIES = frozenset({"STUDENT", "HOMEMAKER", "NOT EMPLOYED"})


_NO_WORK_OCCUPATIONS = _NO_WORK_CATEGORIES | {"HOUSEWIFE", "UNEMPLOYED"}


def _read_status_text(value: object) -> str:
    """Uppercased, stripped text for a status field; '' if missing."""
    return "" if pd.isna(value) else str(value).strip().upper()


# a run of one letter, typed twice or more
_DOUBLED_LETTER_RE = re.compile(r"([A-Z])\1+")


def collapse_doubled_letters(text: str) -> str:
    """Doubled letters typed once: 'NNONE' -> 'NONE'."""
    return _DOUBLED_LETTER_RE.sub(r"\1", text)


def _is_title_word(word: str, last: bool) -> bool:
    """Accept title words and the permitted one-letter spelling errors."""
    if word in JOB_TITLE_HEADS or (not last and word in JOB_TITLE_MODIFIERS):
        return True
    if not last or len(word) < 6:
        return False
    for head in JOB_TITLE_HEADS:
        difference = abs(len(head) - len(word))
        same_length = difference == 0
        one_letter_more_or_less = len(head) >= 8 and difference == 1 and word != head + "S"
        if (same_length or one_letter_more_or_less) and Levenshtein.distance(word, head) == 1:
            return True
    return False


def is_job_title(text: str) -> bool:
    """All title words, the last one naming a job."""
    words = re.findall(r"[A-Z0-9&]+", text)
    return bool(words) and all(_is_title_word(word, index == len(words) - 1) for index, word in enumerate(words))


_ADMIN_NOTE_RE = re.compile(ADMIN_NOTE_EMPLOYER_RE)

_STRUCTURAL_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)

_EMAIL_RE = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")


def employer_rejection_reason(employer: object) -> str:
    """Reject known non-company values; accepted names are not verified."""
    if pd.isna(employer):
        return "missing_value"
    text = re.sub(r"\s+", " ", str(employer)).strip()
    return _find_rejection_reason(text.upper())


@lru_cache(maxsize=32768)
def _find_rejection_reason(upper: str) -> str:
    """Evaluate each distinct normalized name once during a pipeline run."""
    if upper in ("NAN", "NONE", "N/A", "NA", ""):
        return "missing_value"
    # Ignore terminal punctuation on statuses/admin notes, not punctuation inside names.
    canonical = re.sub(r"\s*;\s*", "; ", upper).rstrip(" .;,:!?")
    # RET is ambiguous: no company, and no invented retirement.
    if canonical == "RET":
        return "ambiguous_placeholder"
    # One character or a form placeholder names no company.
    letters = re.sub(r"[^A-Z0-9]", "", upper)
    if len(letters) < 2 or letters == "BLANK":
        return "structural_junk"
    if canonical in NOT_REAL_EMPLOYER:
        return "non_company_value"
    if _ADMIN_NOTE_RE.fullmatch(canonical):
        return "administrative_note"
    if _STRUCTURAL_JUNK_RE.fullmatch(upper) or _EMAIL_RE.fullmatch(upper):
        return "structural_junk"
    if is_job_title(canonical):
        return "job_title"
    if status_word(canonical) or collapse_doubled_letters(canonical) in EMPLOYER_STATUS_VALUES:
        return "status_typo"
    return ""


def is_real_employer(employer: object) -> bool:
    """Whether a value can be an employer name."""
    return not employer_rejection_reason(employer)


def classify_employer_status(employer: object, occupation: object = "", category: object = "") -> str:
    """Classify work status without changing the reported company."""
    employer_text = _read_status_text(employer)
    occupation_text = _read_status_text(occupation)
    category_text = _read_status_text(category)

    if employer_text == "RETIRED":
        return "retired"
    no_work = (
        employer_text in _NO_WORK_OCCUPATIONS
        or category_text in _NO_WORK_CATEGORIES
        or occupation_text in _NO_WORK_OCCUPATIONS
    )
    if no_work:
        return "not_employed"
    if employer_text in {"SELF-EMPLOYED", "SELF EMPLOYED"} or category_text == "SELF-EMPLOYED":
        return "self_employed"
    return "active" if is_real_employer(employer) else "missing"


def classify_employer_statuses(df: pd.DataFrame) -> pd.Series:
    """Classify every row in a frame."""
    blank = pd.Series("", index=df.index)
    employers = df.get("contributor_employer", blank)
    occupations = df.get("contributor_occupation", blank)
    categories = df.get("occupation_category", blank)
    return pd.Series(
        [
            classify_employer_status(employer, occupation, category)
            for employer, occupation, category in zip(
                employers,
                occupations,
                categories,
            )
        ],
        index=df.index,
    )


def current_employer_name(status: object, employer: object) -> str:
    """Return a real current company, only when the status permits one."""
    if status not in {"active", "self_employed"} or not is_real_employer(employer):
        return ""
    return str(employer).strip()


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
