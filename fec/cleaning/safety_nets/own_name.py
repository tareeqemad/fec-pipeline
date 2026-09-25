"""Whether an employer value is the filer's own name."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.not_employers import LEGAL_SUFFIX_RE


def _name_word_set(*parts: str) -> frozenset[str]:
    """Word-set of a personal name: punctuation dropped, single letters ignored, order ignored (FEC stores LAST, FIRST)."""
    words = re.sub(r'[^A-Z]', ' ', ' '.join(parts).upper()).split()
    return frozenset(word for word in words if len(word) > 1)


def _is_own_name(df: pd.DataFrame, idx, employer: str) -> bool:
    """True only when the whole employer value is the donor's name."""
    first = _name_word_set(df.at[idx, 'contributor_first_name'])
    last = _name_word_set(df.at[idx, 'contributor_last_name'])
    if not first or not last:
        return False

    possible = {first | last}
    if 'contributor_middle_name' in df.columns:
        middle = _name_word_set(df.at[idx, 'contributor_middle_name'])
        if middle:
            possible.add(first | middle | last)
    return _name_word_set(employer) in possible


def _had_legal_suffix(df: pd.DataFrame, idx) -> bool:
    """The raw suffix proves an own-named value is a company, not a bare name."""
    if 'contributor_employer_original' not in df.columns:
        return False
    original = str(df.at[idx, 'contributor_employer_original']).strip().upper()
    return bool(LEGAL_SUFFIX_RE.search(original))
