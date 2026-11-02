"""Occupation and occupation_category safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config.occupation_rules.rules import (
    WEB_ARTIFACT_OCCUPATIONS,
)

# A company alone in occupation; a leading role word excluded.
_OCC_IS_COMPANY_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|PC|P\.C|PLLC|LTD|COMPANY)\.?$',
    re.IGNORECASE,
)
# a leading title before a company name: "PRESIDENT, ACME INC"
_OCC_ROLE_PREFIX_RE = re.compile(
    r'^(?:PRESIDENT|VP|VICE PRESIDENT|CEO|CFO|COO|CTO|OWNER|PARTNER|DIRECTOR|'
    r'MANAGER|FOUNDER|PRINCIPAL|CHAIRMAN|EXECUTIVE|MD|DR)\b',
    re.IGNORECASE,
)


# Fill missing categories for individuals, then committees.
def _fill_null_occupation_category(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    n_fixed = 0
    for scope, category in (
        (is_indiv, 'OTHER'),
        (df['entity_type'] == 'COMMITTEE/PAC', 'POLITICAL COMMITTEE'),
    ):
        missing = scope & df['occupation_category'].isna()
        if missing.any():
            df.loc[missing, 'occupation_category'] = category
            n_fixed += int(missing.sum())
    return n_fixed


# fix contradictory NOT DISCLOSED occupation with SELF-EMPLOYED employer
def _fix_bitton_edge_case(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """L. occ='NOT DISCLOSED' + emp='SELF-EMPLOYED' -> contradictory; fix status."""
    mask = (
        is_indiv
        & (df['contributor_occupation'].fillna('').str.upper() == 'NOT DISCLOSED')
        & (df['contributor_employer'].fillna('').str.upper() == 'SELF-EMPLOYED')
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'occupation_category'] = 'SELF-EMPLOYED'
    return n_fixed


# clear web form artifact strings left in occupation field
def _fix_web_artifact_occupation(df: pd.DataFrame) -> int:
    """Web form artifacts in occupation become NaN."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & occ.isin(WEB_ARTIFACT_OCCUPATIONS)
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n_fixed


# clear or relocate non-occupation junk (emails, bare company names)
def _null_junk_occupation(df: pd.DataFrame) -> int:
    """Clear non-occupations; move company names to the employer first."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    is_email = occ.str.contains('@', na=False, regex=False)
    is_company = (occ.str.contains(_OCC_IS_COMPANY_RE, na=False)
                  & ~occ.str.contains(_OCC_ROLE_PREFIX_RE, na=False))

    email_mask = is_indiv & is_email
    company_mask = is_indiv & is_company & ~is_email
    move = company_mask & (emp.eq('') | emp.eq('SELF-EMPLOYED'))
    if move.any():
        df.loc[move, 'contributor_employer'] = df.loc[move, 'contributor_occupation']

    clear = email_mask | company_mask
    n_fixed = int(clear.sum())
    if n_fixed:
        df.loc[clear, 'contributor_occupation'] = np.nan
        df.loc[clear, 'occupation_category'] = 'OTHER'
    return n_fixed


