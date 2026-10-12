"""Address (city/state/ZIP) safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config.constants import (
    FOREIGN_CITIES_NO_US_STATE, AMBIGUOUS_CITIES,
)

_ZIP_CITY = {'10022': 'NEW YORK'}

# census race/ethnicity artifact
_ETHNICITY_RE = re.compile(r'NOT OF HISPANIC|HISPANIC ORIGIN', re.IGNORECASE)

# Virgin Islands ZIP5: 00801 to 00851
_VI_ZIP_RE = re.compile(r'^008[0-4]\d$|^00850$|^00851$')
# a Puerto Rico ZIP5: 006xx, 007xx, 009xx
_PR_ZIP_RE = re.compile(r'^00[679]\d{2}$')


# fill null city from known ZIP lookup
def fill_null_city_from_zip(df: pd.DataFrame) -> int:
    null_city = df['contributor_city'].isna()
    n_fixed = 0
    for idx in df.index[null_city]:
        zip_code = str(df.at[idx, 'contributor_zip'] or '')
        if zip_code in _ZIP_CITY:
            df.at[idx, 'contributor_city'] = _ZIP_CITY[zip_code]
            n_fixed += 1
    return n_fixed


# null foreign city filed with US state
def fix_foreign_addresses(df: pd.DataFrame) -> int:
    city = df['contributor_city'].fillna('').str.strip().str.upper()
    state = df['contributor_state'].fillna('').str.strip().str.upper()

    # unambiguous foreign cities: always flag
    unambiguous = FOREIGN_CITIES_NO_US_STATE - set(AMBIGUOUS_CITIES.keys())
    mask_foreign = city.isin(unambiguous)

    for city_name, valid_states in AMBIGUOUS_CITIES.items():
        is_this_city = city == city_name
        wrong_state = ~state.isin(valid_states)
        mask_foreign = mask_foreign | (is_this_city & wrong_state)

    n_fixed = int(mask_foreign.sum())
    if n_fixed:
        df.loc[mask_foreign, 'contributor_city'] = np.nan
    return n_fixed


# null city names with commas or ethnicity
def fix_garbage_city_names(df: pd.DataFrame) -> int:
    city = df['contributor_city'].fillna('')
    # a real city never contains a comma
    has_comma = city.str.contains(',', na=False, regex=False)
    has_ethnicity = city.str.contains(_ETHNICITY_RE, na=False)
    mask = has_comma | has_ethnicity
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_city'] = np.nan
    return n_fixed


# set PR or VI state from ZIP
def fix_pr_zip_wrong_state(df: pd.DataFrame) -> int:
    zip5 = df['contributor_zip'].fillna('')
    state = df['contributor_state'].fillna('')

    is_vi_zip = zip5.str.match(_VI_ZIP_RE, na=False)
    vi_wrong = is_vi_zip & (state != 'VI')

    is_pr_zip = zip5.str.match(_PR_ZIP_RE, na=False) & ~is_vi_zip
    pr_wrong = is_pr_zip & (state != 'PR')

    n_fixed = 0
    if vi_wrong.any():
        df.loc[vi_wrong, 'contributor_state'] = 'VI'
        n_fixed += int(vi_wrong.sum())
    if pr_wrong.any():
        df.loc[pr_wrong, 'contributor_state'] = 'PR'
        n_fixed += int(pr_wrong.sum())
    return n_fixed
