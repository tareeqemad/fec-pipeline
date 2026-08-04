"""Occupation, occupation_category and occupation_status safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.occupations import _categorize
from fec.config.constants import NOT_EMPLOYED_VARIANTS, SKIP_EMPLOYERS, STATUS_WORDS
from fec.config.occupation_rules import (
    RECLASSIFY_CATEGORY_RULES,
    WEB_ARTIFACT_OCCUPATIONS,
)

# a company name (legal suffix) alone in the occupation field; a leading
# role word ("PRESIDENT, X INC") is a real title, so it's excluded
_OCC_IS_COMPANY_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|PC|P\.C|PLLC|LTD|COMPANY)\.?$', re.I)
_OCC_ROLE_PREFIX_RE = re.compile(
    r'^(?:PRESIDENT|VP|VICE PRESIDENT|CEO|CFO|COO|CTO|OWNER|PARTNER|DIRECTOR|'
    r'MANAGER|FOUNDER|PRINCIPAL|CHAIRMAN|EXECUTIVE|MD|DR)\b', re.I)



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


def _fill_null_occupation_category(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """I. occupation_category NULL -> fill based on entity type."""
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


def _fix_not_disclosed_in_other(df: pd.DataFrame) -> int:
    """AN. occupation='NOT DISCLOSED' stays in the reportable OTHER category."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    mask = (
        is_indiv
        & (df['contributor_occupation'] == 'NOT DISCLOSED')
        & (df['occupation_category'] == 'OTHER')
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'occupation_status'] = 'NOT_DISCLOSED'
    return n_fixed


def _fix_web_artifact_occupation(df: pd.DataFrame) -> int:
    """AP. Web form artifacts in occupation ('LOADING', 'ACMIO', 'REMD') -> NaN."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & occ.isin(WEB_ARTIFACT_OCCUPATIONS)
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n_fixed


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


def _null_junk_occupation(df: pd.DataFrame) -> int:
    """AR. Null non-occupation values (category OTHER): emails always; a bare company name is MOVED to an empty/SELF-EMPLOYED employer (never destroyed), nulled only when the employer already has a value."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    is_email = occ.str.contains('@', na=False)
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


def _fix_emp_occ_category_consistency(df: pd.DataFrame) -> int:
    """AR. Cross-field employer/occupation/category consistency; a real-company employer wins over a RETIRED occupation or SELF-EMPLOYED category."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    categories = df['occupation_category'].fillna('')
    n_fixed = 0

    # The FEC sometimes carries NOT EMPLOYED in the employer field while the
    # same filing explicitly says RETIRED in occupation. Retirement is the
    # more specific status; no company or profession is inferred here.
    retired_not_employed = (
        is_indiv
        & emp.isin(NOT_EMPLOYED_VARIANTS)
        & occ.eq('RETIRED')
        & categories.eq('RETIRED')
    )
    n_retired_not_employed = int(retired_not_employed.sum())
    if n_retired_not_employed:
        df.loc[retired_not_employed, 'contributor_employer'] = 'RETIRED'
        df.loc[retired_not_employed, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += n_retired_not_employed

    ret_wrong_cat = (
        is_indiv & (emp == 'RETIRED')
        & (categories != 'RETIRED')
        & occ.isin({'RETIRED', ''})
    )
    n_retired = int(ret_wrong_cat.sum())
    if n_retired:
        df.loc[ret_wrong_cat, 'contributor_occupation'] = 'RETIRED'
        df.loc[ret_wrong_cat, 'occupation_category'] = 'RETIRED'
        df.loc[ret_wrong_cat, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += n_retired

    not_employed_wrong_cat = (
        is_indiv & (emp == 'NOT EMPLOYED')
        & (categories != 'NOT EMPLOYED')
        & occ.isin({'NOT EMPLOYED', 'UNEMPLOYED', ''})
    )
    n_not_employed = int(not_employed_wrong_cat.sum())
    if n_not_employed:
        df.loc[not_employed_wrong_cat, 'contributor_occupation'] = 'NOT EMPLOYED'
        df.loc[not_employed_wrong_cat, 'occupation_category'] = 'NOT EMPLOYED'
        df.loc[not_employed_wrong_cat, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += n_not_employed

    homemaker_wrong_cat = (
        is_indiv & (emp == 'HOMEMAKER')
        & (categories != 'HOMEMAKER')
        & occ.isin({'HOMEMAKER', 'HOUSEWIFE', ''})
    )
    n_homemaker = int(homemaker_wrong_cat.sum())
    if n_homemaker:
        df.loc[homemaker_wrong_cat, 'contributor_occupation'] = 'HOMEMAKER'
        df.loc[homemaker_wrong_cat, 'occupation_category'] = 'HOMEMAKER'
        df.loc[homemaker_wrong_cat, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += n_homemaker

    # category=SELF-EMPLOYED but employer is a real company: re-derive from occupation
    se_cat_real_emp = (
        is_indiv & (categories == 'SELF-EMPLOYED')
        & ~emp.isin(SKIP_EMPLOYERS) & (emp != '')
    )
    if se_cat_real_emp.any():
        real_occ = df.loc[se_cat_real_emp, 'contributor_occupation'].fillna('')
        new_cats = _categorize(real_occ)
        needs_fix = se_cat_real_emp & (new_cats != 'SELF-EMPLOYED') & new_cats.notna()
        n_se_cat = int(needs_fix.sum())
        if n_se_cat:
            df.loc[needs_fix, 'occupation_category'] = new_cats[needs_fix]
            df.loc[needs_fix, 'occupation_status'] = 'DISCLOSED'
            n_fixed += n_se_cat

    return n_fixed


def _fix_slash_occupation(df: pd.DataFrame) -> int:
    """AS. Slash occupation (INVESTOR/DEVELOPER): categorize on the first part, but only when the category is OTHER or NULL."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    categories = df['occupation_category'].fillna('')

    has_slash = is_indiv & occ.str.contains('/', na=False) & categories.isin({'OTHER', ''})
    n_candidates = int(has_slash.sum())
    if not n_candidates:
        return 0

    first_part = occ[has_slash].str.split('/').str[0].str.strip()
    new_cats = _categorize(first_part)
    improved = has_slash & new_cats.notna() & (new_cats != 'OTHER')
    n_fixed = int(improved.sum())
    if n_fixed:
        df.loc[improved, 'occupation_category'] = new_cats[improved]
    return n_fixed


def _reclassify_other_category(df: pd.DataFrame) -> int:
    """AT. Second pass: reclassify OTHER occupation_category by keyword match."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    is_other = is_indiv & (df['occupation_category'] == 'OTHER')
    occ = df.loc[is_other, 'contributor_occupation'].fillna('')

    if occ.empty:
        return 0

    n_fixed = 0
    for pattern, category in RECLASSIFY_CATEGORY_RULES:
        matches = is_other & occ.str.contains(pattern, case=False, na=False)
        # only records still in OTHER (avoid double-counting)
        still_other = matches & (df['occupation_category'] == 'OTHER')
        count = int(still_other.sum())
        if count:
            df.loc[still_other, 'occupation_category'] = category
            n_fixed += count

    return n_fixed
