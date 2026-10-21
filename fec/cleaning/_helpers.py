"""Shared cleaning helpers."""
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd


def read_raw_employer_evidence(path) -> pd.DataFrame:
    """Read employer evidence once per file version; callers must not mutate it."""
    path = Path(path).resolve()
    stat = path.stat()
    return _read_raw_employer_evidence(path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=1)
def _read_raw_employer_evidence(path: Path, mtime_ns: int, size: int) -> pd.DataFrame:
    return pd.read_csv(
        path,
        usecols=lambda column: column in (
            'sub_id', 'contributor_name', 'contributor_employer',
        ),
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )


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
    df.loc[idx, 'occupation_status'] = 'MISSING'


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
