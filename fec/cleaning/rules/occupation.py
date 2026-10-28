"""Occupation, occupation_category and occupation_status safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config.constants import STATUS_WORDS
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


# fix status when marked DISCLOSED but employer is missing
def _fix_disclosed_no_employer(df: pd.DataFrame) -> int:
    """D. DISCLOSED but no employer -> fix status."""
    bad = (
        (df['occupation_status'] == 'DISCLOSED')
        & df['contributor_employer'].isna()
        & (df['entity_type'] == 'INDIVIDUAL')
    )
    n_fixed = int(bad.sum())
    if not n_fixed:
        return 0

    occ = df['contributor_occupation'].fillna('').str.upper()
    is_status = occ.isin(STATUS_WORDS)
    df.loc[bad & is_status, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[bad & ~is_status, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n_fixed


# categorize EMPLOYED occupation with no category as OTHER
def _fix_employed_no_category(df: pd.DataFrame) -> int:
    """E. occ='EMPLOYED' with no category -> OTHER."""
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_occupation'] == 'EMPLOYED')
        & df['occupation_category'].isna()
    )
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    df.loc[mask, 'occupation_category'] = 'OTHER'
    emp_is_emp = mask & (df['contributor_employer'].fillna('').str.upper() == 'EMPLOYED')
    df.loc[emp_is_emp, 'contributor_employer'] = np.nan
    df.loc[emp_is_emp, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n_fixed


# mark status word in occupation as NOT_APPLICABLE
def _fix_status_word_in_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """G. EMPLOYER_MISSING/NOT_DISCLOSED + status word in occupation -> NOT_APPLICABLE."""
    mask = (
        is_indiv
        & df['occupation_status'].isin(['NOT_DISCLOSED', 'EMPLOYER_MISSING'])
        & df['contributor_occupation'].isin(STATUS_WORDS)
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    return n_fixed


# reclassify NOT_DISCLOSED rows that actually have a real occupation
def _fix_not_disclosed_with_real_occ(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """H. NOT_DISCLOSED with real occupation -> EMPLOYER_MISSING."""
    mask = (
        is_indiv
        & (df['occupation_status'] == 'NOT_DISCLOSED')
        & df['contributor_occupation'].notna()
        & ~df['contributor_occupation'].isin(STATUS_WORDS)
        & df['contributor_employer'].isna()
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n_fixed


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
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
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


# derive a real occupation when occupation is just EMPLOYED
def _fix_employed_as_occupation(df: pd.DataFrame) -> int:
    """AQ. occupation='EMPLOYED' -> derive real occupation from employer context."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    mask = is_indiv & (df['contributor_occupation'] == 'EMPLOYED')
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    self_emp = mask & (df['contributor_employer'].fillna('') == 'SELF-EMPLOYED')
    if self_emp.any():
        df.loc[self_emp, 'contributor_occupation'] = 'SELF-EMPLOYED'
        df.loc[self_emp, 'occupation_category'] = 'SELF-EMPLOYED'
        df.loc[self_emp, 'occupation_status'] = 'NOT_APPLICABLE'

    # EMPLOYED alone is not informative
    rest = mask & ~self_emp
    if rest.any():
        df.loc[rest, 'occupation_category'] = 'OTHER'
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


