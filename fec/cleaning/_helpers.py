"""Shared helpers for the enhancement sub-modules."""
import numpy as np
import pandas as pd


# normalize a series for safe comparison
def _norm(s: pd.Series) -> pd.Series:
    """Normalize a Series for safe comparison: NaN -> '', strip, upper."""
    return s.fillna('').astype(str).str.strip().str.upper()


# return the index of all individual rows
def _indiv_idx(df: pd.DataFrame) -> pd.Index:
    """Return the index of all INDIVIDUAL rows."""
    return df.index[df['entity_type'] == 'INDIVIDUAL']


# mark rows as missing occupation
def _set_missing(df: pd.DataFrame, idx) -> None:
    """Mark rows as missing occupation (NaN + MISSING status)."""
    df.loc[idx, 'contributor_occupation'] = np.nan
    df.loc[idx, 'occupation_category'] = pd.NA
    df.loc[idx, 'occupation_status'] = 'MISSING'


# compute levenshtein edit distance between two strings
def levenshtein(a: str, b: str) -> int:
    """Levenshtein edit distance between two strings."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    previous_row = list(range(len(b) + 1))
    for char_a in a:
        current_row = [previous_row[0] + 1]
        for position, char_b in enumerate(b):
            current_row.append(min(previous_row[position + 1] + 1, current_row[position] + 1, previous_row[position] + (char_a != char_b)))
        previous_row = current_row
    return previous_row[-1]
