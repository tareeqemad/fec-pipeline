"""Address (city/state/ZIP) safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

# census race/ethnicity artifact
_ETHNICITY_RE = re.compile(r'NOT OF HISPANIC|HISPANIC ORIGIN', re.IGNORECASE)

# VI ZIPs: 00801-00851; PR ZIPs: 00600-00799, 00900-00999 (excluding VI range)
_VI_ZIP_RE = re.compile(r'^008[0-4]\d$|^00850$|^00851$')
# a Puerto Rico ZIP5: 006xx, 007xx, 009xx
_PR_ZIP_RE = re.compile(r'^00[679]\d{2}$')


# null garbage city names such as commas or ethnicity text
def _fix_garbage_city_names(df: pd.DataFrame) -> int:
    """Garbage city names become NaN."""
    city = df['contributor_city'].fillna('')
    # A city never contains a comma.
    has_comma = city.str.contains(',', na=False, regex=False)
    has_ethnicity = city.str.contains(_ETHNICITY_RE, na=False)
    mask = has_comma | has_ethnicity
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_city'] = np.nan
    return n_fixed


# fix state to PR or VI to match the ZIP
def _fix_pr_zip_wrong_state(df: pd.DataFrame) -> int:
    """Puerto Rico/VI ZIP with a wrong state: fix the state."""
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
