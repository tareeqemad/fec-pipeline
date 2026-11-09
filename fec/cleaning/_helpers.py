"""Shared cleaning helpers."""
import re

import numpy as np
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


def _map_occupations(df: pd.DataFrame, mapping: dict) -> tuple[pd.DataFrame, int]:
    """Map individual occupations; return the frame and changed row count."""
    individuals = _indiv_idx(df)
    occupation = df.loc[individuals, 'contributor_occupation']
    hits = individuals[occupation.isin(mapping)]
    if len(hits):
        df.loc[hits, 'contributor_occupation'] = occupation[hits].map(mapping)
    return df, int(len(hits))


def _set_missing(df: pd.DataFrame, idx) -> None:
    """Clear occupation and category; mark occupation as missing."""
    df.loc[idx, 'contributor_occupation'] = np.nan
    df.loc[idx, 'occupation_category'] = pd.NA

