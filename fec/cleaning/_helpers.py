"""Shared helpers for the enhancement sub-modules."""
import numpy as np
import pandas as pd


# normalize a series for safe comparison
def upper_text(s: pd.Series) -> pd.Series:
    return s.fillna('').astype(str).str.strip().str.upper()


# return the index of all individual rows
def individual_index(df: pd.DataFrame) -> pd.Index:
    return df.index[df['entity_type'] == 'INDIVIDUAL']


# remap individuals' occupations; return changed count
def map_occupations(df: pd.DataFrame, mapping: dict) -> tuple[pd.DataFrame, int]:
    individuals = individual_index(df)
    occupation = df.loc[individuals, 'contributor_occupation']
    hits = individuals[occupation.isin(mapping)]
    if len(hits):
        df.loc[hits, 'contributor_occupation'] = occupation[hits].map(mapping)
    return df, int(len(hits))


# mark rows as missing occupation
def set_missing_occupation(df: pd.DataFrame, idx) -> None:
    df.loc[idx, 'contributor_occupation'] = np.nan
    df.loc[idx, 'occupation_category'] = pd.NA
    df.loc[idx, 'occupation_status'] = 'MISSING'


# compute levenshtein edit distance between two strings
def levenshtein(a: str, b: str) -> int:
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
