"""Occupation, occupation_category and occupation_status safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config.constants import STATUS_WORDS
from fec.config.occupation_rules.rules import (
    WEB_ARTIFACT_OCCUPATIONS,
)

# occupation ending in a legal suffix
_OCC_IS_COMPANY_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|PC|P\.C|PLLC|LTD|COMPANY)\.?$',
    re.IGNORECASE,
)
# a leading role makes it a title
_OCC_ROLE_PREFIX_RE = re.compile(
    r'^(?:PRESIDENT|VP|VICE PRESIDENT|CEO|CFO|COO|CTO|OWNER|PARTNER|DIRECTOR|'
    r'MANAGER|FOUNDER|PRINCIPAL|CHAIRMAN|EXECUTIVE|MD|DR)\b',
    re.IGNORECASE,
)


# fix DISCLOSED status when employer is missing
def fix_disclosed_no_employer(df: pd.DataFrame) -> int:
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


# uncategorized EMPLOYED occupation becomes OTHER
def fix_employed_no_category(df: pd.DataFrame) -> int:
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
def fix_status_word_in_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    mask = (
        is_indiv
        & df['occupation_status'].isin(['NOT_DISCLOSED', 'EMPLOYER_MISSING'])
        & df['contributor_occupation'].isin(STATUS_WORDS)
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    return n_fixed


# NOT_DISCLOSED with real occupation: EMPLOYER_MISSING
def fix_not_disclosed_with_real_occ(df: pd.DataFrame, is_indiv: pd.Series) -> int:
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


# fill null occupation_category based on entity type
def fill_null_occupation_category(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    n_fixed = 0
    null_indiv = is_indiv & df['occupation_category'].isna()
    if null_indiv.any():
        df.loc[null_indiv, 'occupation_category'] = 'OTHER'
        n_fixed += int(null_indiv.sum())

    null_comm = (df['entity_type'] == 'COMMITTEE/PAC') & df['occupation_category'].isna()
    if null_comm.any():
        df.loc[null_comm, 'occupation_category'] = 'POLITICAL COMMITTEE'
        n_fixed += int(null_comm.sum())
    return n_fixed


# NOT DISCLOSED occupation with SELF-EMPLOYED employer
def fix_bitton_edge_case(df: pd.DataFrame, is_indiv: pd.Series) -> int:
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


# clear web-form artifacts like 'LOADING' from occupation
def fix_web_artifact_occupation(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & occ.isin(WEB_ARTIFACT_OCCUPATIONS)
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n_fixed


# handle bare EMPLOYED occupation using the employer
def fix_employed_as_occupation(df: pd.DataFrame) -> int:
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


# null emails; move company names to employer
def null_junk_occupation(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    is_email = occ.str.contains('@', na=False, regex=False)
    is_company = (occ.str.contains(_OCC_IS_COMPANY_RE, na=False)
                  & ~occ.str.contains(_OCC_ROLE_PREFIX_RE, na=False))

    email_mask = is_indiv & is_email
    n_fixed = int(email_mask.sum())
    if email_mask.any():
        df.loc[email_mask, 'contributor_occupation'] = np.nan
        df.loc[email_mask, 'occupation_category'] = 'OTHER'

    company_mask = is_indiv & is_company & ~is_email
    if company_mask.any():
        move = company_mask & (emp.eq('') | emp.eq('SELF-EMPLOYED'))
        if move.any():
            df.loc[move, 'contributor_employer'] = df.loc[move, 'contributor_occupation']
            df.loc[move, 'contributor_occupation'] = np.nan
            df.loc[move, 'occupation_category'] = 'OTHER'
            n_fixed += int(move.sum())
        null_occ = company_mask & ~move
        if null_occ.any():
            df.loc[null_occ, 'contributor_occupation'] = np.nan
            df.loc[null_occ, 'occupation_category'] = 'OTHER'
            n_fixed += int(null_occ.sum())
    return n_fixed


