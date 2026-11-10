"""One reading of a status word written with a slip.

RETIRED, SELF-EMPLOYED, NOT EMPLOYED, UNEMPLOYED, HOMEMAKER, HOUSEWIFE and
STUDENT are filed in every spelling: RETIERD, SELF EMPLOYEED, SE;F EMPLOYED,
NOT EMOLOYED, SELFEMPLOYED, SELF-EMPLO. status_word reads them all the same
way: the letters alone, compared with the letters of each status word, are
one slip apart (two for the long ones), or are the start of SELF-EMPLOYED.
A text that already is a status word is returned as itself.

What is not a slip stays for the spelling table (employer_spellings.csv):
a different word for the same thing (MYSELF, SOLE PROPRIETOR, JOBLESS,
NOT WORKING) and a status beside other words (MOSTLY RETIRED), which the
RETIRED reader handles.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from typing import TYPE_CHECKING

from rapidfuzz.distance import OSA

if TYPE_CHECKING:
    import pandas as pd

# status word -> its letters alone
_STATUSES = {
    "RETIRED": "RETIRED",
    "SELF-EMPLOYED": "SELFEMPLOYED",
    "NOT EMPLOYED": "NOTEMPLOYED",
    "UNEMPLOYED": "UNEMPLOYED",
    "HOMEMAKER": "HOMEMAKER",
    "HOUSEWIFE": "HOUSEWIFE",
    "STUDENT": "STUDENT",
}

_LETTERS_RE = re.compile(r"[^A-Z]")

_MIN_PREFIX = 7  # SELFEMP: a truncated SELF-EMPLOYED


def _count_slips_allowed(letters: str) -> int:
    """Return how many slips a status word of this length allows."""
    return 2 if len(letters) >= 10 else 1


@lru_cache(maxsize=65536)
def status_word(text: object) -> str | None:
    """Return the status a text means, or None when it names something else."""
    if not isinstance(text, str):
        return None
    letters = _LETTERS_RE.sub("", text.upper())
    if len(letters) < 6:
        return None
    best, best_distance = None, 99
    for status, target in _STATUSES.items():
        if letters == target:
            return status
        allowed = _count_slips_allowed(target)
        distance = OSA.distance(letters, target, score_cutoff=allowed)
        if distance <= allowed and distance < best_distance:
            best, best_distance = status, distance
    if best is not None:
        return best
    if len(letters) >= _MIN_PREFIX and "SELFEMPLOYED".startswith(letters):
        return "SELF-EMPLOYED"
    return None


def learn_status_typos(texts: Iterable[object], canonical: dict | None = None) -> dict[str, str]:
    """{filed text: status word} for every text that is a status with a slip.

    canonical maps a status to the form the tables use (UNEMPLOYED -> NOT EMPLOYED).
    """
    canonical = canonical or {}
    learnt = {}
    for value in set(texts):
        if not isinstance(value, str):
            continue
        plain = " ".join(value.upper().split())
        status = status_word(plain)
        if not status:
            continue
        status = canonical.get(status, status)
        if plain != status:
            learnt[plain] = status
    return learnt


def extend_spellings(df: pd.DataFrame) -> int:
    """Read the filed work fields once; add the status slips to the spelling maps."""
    import pandas as pd

    from fec.vocabulary.occupations import EMPLOYER_NORMALIZE, OCCUPATION_NORMALIZE

    texts = pd.concat([df["contributor_employer"], df["contributor_occupation"]]).dropna()
    learnt = learn_status_typos(texts, canonical=EMPLOYER_NORMALIZE)
    for text, status in learnt.items():
        EMPLOYER_NORMALIZE.setdefault(text, status)
        OCCUPATION_NORMALIZE.setdefault(text, status)
    return len(learnt)
