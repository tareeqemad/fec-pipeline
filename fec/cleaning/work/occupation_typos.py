"""Misspelt occupations: one rule instead of a table row for each.

A filed text one slip away from a listed occupation names that occupation:
a letter dropped, added, swapped or transposed (ATTORNY, CONSUTLANT,
PHYSICAIN). The rule reads the filed texts once, before the work fields are
cleaned, and adds what it learns to the occupation corrections, so every
later step treats a learnt typo like a reviewed row.

What the rule leaves alone, for the table: a slip in a word under six
letters (CO / CEO), a text as close to two listed occupations, a difference
only in spaces or punctuation (text normalisation reads those), a status
word (the RETIRED reader owns those), and any text already listed or
already corrected. Only the occupation box is read: a company name in the
employer box can be one letter from a job (GARTNER / PARTNER).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd
from rapidfuzz import process
from rapidfuzz.distance import OSA

from fec.vocabulary.occupations import (
    CATEGORY_OVERRIDES,
    OCCUPATION_CORRECTIONS,
    OCCUPATION_NORMALIZE,
    TEXT_CORRECTIONS,
)
from fec.vocabulary.statuses import STATUS_WORDS

MIN_WORD_LETTERS = 6

_TEXT_RE = re.compile(r"[A-Z][A-Z '&/.-]*")


def _to_plain_text(value: object) -> str:
    """Return a text uppercased with single spaces, or '' if it is not a string."""
    return " ".join(value.upper().split()) if isinstance(value, str) else ""


def _is_one_word_slip(text: str, listed: str) -> bool:
    """Exactly one word differs, by one slip, and that word is long enough."""
    text_words, listed_words = text.split(), listed.split()
    if len(text_words) != len(listed_words):
        return False
    differing = [(filed, known) for filed, known in zip(text_words, listed_words) if filed != known]
    if len(differing) != 1:
        return False
    filed, known = differing[0]
    letters = re.sub(r"[^A-Z]", "", known)
    return len(letters) >= MIN_WORD_LETTERS and filed.isalpha() and known.isalpha() and OSA.distance(filed, known) == 1


def learn_typos(texts: Iterable[object], listed: Iterable[str] | None = None) -> dict[str, str]:
    """{filed text: listed occupation} for every text one slip from one listed occupation."""
    candidates = CATEGORY_OVERRIDES if listed is None else listed
    listed = [
        occupation
        for occupation in candidates
        if occupation not in OCCUPATION_CORRECTIONS and occupation not in STATUS_WORDS
    ]
    known = set(listed)
    learnt: dict[str, str] = {}
    for text in set(map(_to_plain_text, texts)):
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
