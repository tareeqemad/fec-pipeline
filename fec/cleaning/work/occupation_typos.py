"""A filed occupation one slip from one listed occupation (ATTORNY, PHYSICAIN) is added to the corrections.

Left for the table: a slip in a word under six letters (CO / CEO), a text as close to two occupations, and
status words. Only the occupation box is read: an employer can be one letter from a job (GARTNER / PARTNER).
"""

import re
from collections.abc import Iterable

import pandas as pd
from rapidfuzz import process
from rapidfuzz.distance import OSA

from fec.common.text import upper_single_spaced
from fec.vocabulary.occupations import (
    OCCUPATION_CATEGORIES,
    OCCUPATION_CORRECTIONS,
    OCCUPATION_NORMALIZE,
    TEXT_CORRECTIONS,
)
from fec.vocabulary.statuses import STATUS_WORDS

_MIN_WORD_LETTERS = 6
_TEXT_RE = re.compile(r"[A-Z][A-Z '&/.-]*")


def _is_one_word_slip(text: str, listed: str) -> bool:
    text_words, listed_words = text.split(), listed.split()
    if len(text_words) != len(listed_words):
        return False
    differing = [(filed, known) for filed, known in zip(text_words, listed_words) if filed != known]
    if len(differing) != 1:
        return False
    filed, known = differing[0]
    letters = re.sub(r"[^A-Z]", "", known)
    return len(letters) >= _MIN_WORD_LETTERS and filed.isalpha() and known.isalpha() and OSA.distance(filed, known) == 1


def learn_typos(texts: Iterable[object], listed: Iterable[str] | None = None) -> dict[str, str]:
    """{filed text: listed occupation} for every text one slip from one listed occupation."""
    candidates = OCCUPATION_CATEGORIES if listed is None else listed
    listed = [text for text in candidates if text not in OCCUPATION_CORRECTIONS and text not in STATUS_WORDS]
    known = set(listed)
    learnt: dict[str, str] = {}
    for text in {upper_single_spaced(value) for value in texts if isinstance(value, str)}:
        if not text or text in known or text in OCCUPATION_CORRECTIONS or not _TEXT_RE.fullmatch(text):
            continue
        near = process.extract(text, listed, scorer=OSA.distance, score_cutoff=1, limit=2)
        if len(near) == 1 and _is_one_word_slip(text, near[0][0]):
            learnt[text] = near[0][0]
    return learnt


def extend_corrections(df: pd.DataFrame) -> int:
    """Learn typos from the filed occupations and add them to the corrections."""
    learnt = learn_typos(df["contributor_occupation"].dropna())
    OCCUPATION_CORRECTIONS.update(learnt)
    TEXT_CORRECTIONS.update(learnt)
    OCCUPATION_NORMALIZE.update(learnt)
    return len(learnt)
