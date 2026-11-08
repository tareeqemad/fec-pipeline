"""Employer-field junk clearing; swap and self-employment fixes live in employer_swaps."""
from __future__ import annotations

import numpy as np
import pandas as pd

from fec.cleaning.work.normalize import (
    _categorize,
    swap_employer_and_occupation,
)
from fec.cleaning.work.company import is_company
from fec.config.not_employers import SECTOR_AS_EMPLOYER

# EXECUTIVE is a title when beside a company.
_TITLE_SECTOR_WORDS = frozenset({'EXECUTIVE'}) & SECTOR_AS_EMPLOYER

# The occupation names a company (see fec.cleaning.work.company).
def _occupation_names_a_company(occ: pd.Series, candidates: pd.Series) -> pd.Series:
    result = pd.Series(False, index=occ.index)
    if candidates.any():
        result.loc[candidates] = is_company(occ[candidates]).to_numpy()
    return result


# Clear sector names; first recover a company or move titles.
def _null_sector_as_employer(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & emp.isin(SECTOR_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    title = mask & emp.isin(_TITLE_SECTOR_WORDS)
    swap = _occupation_names_a_company(occ, title & occ.ne(''))
    if swap.any():
        swap_employer_and_occupation(df, swap)
    to_occupation = title & occ.eq('')
    if to_occupation.any():
        df.loc[to_occupation, 'contributor_occupation'] = emp[to_occupation]
        df.loc[to_occupation, 'occupation_category'] = _categorize(emp[to_occupation])

    df.loc[mask & ~swap, 'contributor_employer'] = np.nan
    return n_fixed


# remove the web-form --CHOOSE-- prefix from employer names
def _fix_choose_prefix(df: pd.DataFrame) -> int:
    """Remove the web-form --CHOOSE-- prefix from employer names."""
    employer = df['contributor_employer'].fillna('')
    mask = employer.str.startswith('--CHOOSE--')
    changed = int(mask.sum())
    if changed:
        cleaned = employer[mask].str.removeprefix('--CHOOSE--').str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return changed
