"""A status word filed with a slip (RETIERD, SE;F EMPLOYED, SELF-EMPLO) is read as that status.

A different word for the same thing (MYSELF, JOBLESS) stays for employer_spellings.csv.
"""

import re
from functools import lru_cache

import pandas as pd
from rapidfuzz.distance import OSA

from fec.vocabulary.occupations import EMPLOYER_NORMALIZE, OCCUPATION_NORMALIZE
from fec.vocabulary.tables import ordered_words

_LETTERS_RE = re.compile(r"[^A-Z]")
# status word -> its letters alone; on a tie the earlier status wins
_STATUSES = {status: _LETTERS_RE.sub("", status) for status in ordered_words("status_slip_targets")}
_MIN_PREFIX = 7  # SELFEMP: a truncated SELF-EMPLOYED


@lru_cache(maxsize=65536)
def status_word(text: object) -> str | None:
    """Return the status a text means: one slip away (two for long words) or a start of SELF-EMPLOYED."""
    if not isinstance(text, str):
        return None
    letters = _LETTERS_RE.sub("", text.upper())
    if len(letters) < 6:
        return None
    best, best_distance = None, 99
    for status, target in _STATUSES.items():
        if letters == target:
            return status
        allowed = 2 if len(target) >= 10 else 1
        distance = OSA.distance(letters, target, score_cutoff=allowed)
        if distance <= allowed and distance < best_distance:
            best, best_distance = status, distance
    if best is not None:
        return best
    if len(letters) >= _MIN_PREFIX and "SELFEMPLOYED".startswith(letters):
        return "SELF-EMPLOYED"
    return None


def extend_spellings(df: pd.DataFrame) -> int:
    """Read the filed work fields once; add the status slips to the spelling maps."""
    learnt = {}
    for value in set(pd.concat([df["contributor_employer"], df["contributor_occupation"]]).dropna().astype(str)):
        plain = " ".join(value.upper().split())
        status = status_word(plain)
        if not status:
            continue
        # The form the tables use: UNEMPLOYED -> NOT EMPLOYED.
        status = EMPLOYER_NORMALIZE.get(status, status)
        if plain != status:
            learnt[plain] = status
    for text, status in learnt.items():
        EMPLOYER_NORMALIZE.setdefault(text, status)
        OCCUPATION_NORMALIZE.setdefault(text, status)
    return len(learnt)
