"""
cleaning/_helpers.py — Shared utility functions for enhancement sub-modules.

Used by: enhancements.py, employer_synonyms.py, entity_classification.py, safety_nets.py
"""
import numpy as np
import pandas as pd


def _norm(s: pd.Series) -> pd.Series:
    """Normalize a Series for safe comparison: NaN→'', strip, upper."""
    return s.fillna('').astype(str).str.strip().str.upper()


def _indiv_idx(df: pd.DataFrame) -> pd.Index:
    """Return the index of all INDIVIDUAL rows."""
    return df.index[df['entity_type'] == 'INDIVIDUAL']


def _set_missing(df: pd.DataFrame, idx) -> None:
    """Mark rows as missing occupation (NaN + MISSING status)."""
    df.loc[idx, 'contributor_occupation'] = np.nan
    df.loc[idx, 'occupation_category'] = pd.NA
    df.loc[idx, 'occupation_status'] = 'MISSING'


def levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for c1 in a:
        curr = [prev[0] + 1]
        for j, c2 in enumerate(b):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]
