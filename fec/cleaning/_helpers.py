"""Shared cleaning helpers."""
import re

import pandas as pd


def name_words(*parts) -> frozenset[str]:
    """Word set of a personal name, ignoring order, punctuation and initials."""
    text = ' '.join(str(part) for part in parts if isinstance(part, str))
    return frozenset(word for word in re.sub(r'[^A-Z]', ' ', text.upper()).split() if len(word) > 1)


def _norm(s: pd.Series) -> pd.Series:
    """Fill blanks, trim spaces and uppercase text."""
    return s.fillna('').astype(str).str.strip().str.upper()


def _indiv_idx(df: pd.DataFrame) -> pd.Index:
    """Return the index of all INDIVIDUAL rows."""
    return df.index[df['entity_type'] == 'INDIVIDUAL']

