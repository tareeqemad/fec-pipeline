"""cleaning/safety_nets/occupation.py — occupation & occupation_category safety-net fixes."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from fec.config.constants import (
    STATUS_WORDS, SKIP_EMPLOYERS,
)
from fec.cleaning.occupations import _categorize

# A company name (legal suffix) standing alone in the occupation field — the
# occupation should be a role/title, not a company. A leading role word
# ("PRESIDENT, X INC") is a real title, so it's excluded.
_OCC_IS_COMPANY_RE = re.compile(
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|PC|P\.C|PLLC|LTD|COMPANY)\.?$', re.I)
_OCC_ROLE_PREFIX_RE = re.compile(
    r'^(?:PRESIDENT|VP|VICE PRESIDENT|CEO|CFO|COO|CTO|OWNER|PARTNER|DIRECTOR|'
    r'MANAGER|FOUNDER|PRINCIPAL|CHAIRMAN|EXECUTIVE|MD|DR)\b', re.I)


def _null_junk_occupation(df: pd.DataFrame) -> int:
    """AR. Clear occupation values that aren't occupations:
      - an email address ("ADAM.ARENS@PATRIOTSUBARU.COM") -> always nulled.
      - a bare company name (legal suffix, no leading role word). NEVER just
        discarded — that would destroy a real company name:
          * employer is empty or SELF-EMPLOYED -> the company IS the real
            employer the filer misplaced -> MOVE it to the employer field.
          * employer already holds a (real or status) value -> the occ company
            is redundant or a former employer -> null the occupation only.
    Nulled occupations get category OTHER."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    is_email = occ.str.contains('@', na=False)
    is_company = (occ.str.contains(_OCC_IS_COMPANY_RE, na=False)
                  & ~occ.str.contains(_OCC_ROLE_PREFIX_RE, na=False))

    # 1. email anywhere in the occupation -> null
    email_mask = is_indiv & occ.ne('') & is_email
    n = int(email_mask.sum())
    if email_mask.any():
        df.loc[email_mask, 'contributor_occupation'] = np.nan
        if 'occupation_category' in df.columns:
            df.loc[email_mask, 'occupation_category'] = 'OTHER'

    comp_mask = is_indiv & occ.ne('') & is_company & ~is_email
    if comp_mask.any():
        # 2a. employer is empty / self-employed -> the company is the real
        #     employer -> move it across (don't lose it)
        move = comp_mask & (emp.eq('') | emp.eq('SELF-EMPLOYED'))
        if move.any():
            df.loc[move, 'contributor_employer'] = df.loc[move, 'contributor_occupation']
            df.loc[move, 'contributor_occupation'] = np.nan
            if 'occupation_category' in df.columns:
                df.loc[move, 'occupation_category'] = 'OTHER'
            n += int(move.sum())
        # 2b. employer already has a value -> occ company is redundant -> null occ
        null_occ = comp_mask & ~move
        if null_occ.any():
            df.loc[null_occ, 'contributor_occupation'] = np.nan
            if 'occupation_category' in df.columns:
                df.loc[null_occ, 'occupation_category'] = 'OTHER'
            n += int(null_occ.sum())
    return n


def _fix_disclosed_no_employer(df: pd.DataFrame) -> int:
    """D. DISCLOSED but no employer → fix status."""
    bad = (
        (df['occupation_status'] == 'DISCLOSED')
        & df['contributor_employer'].isna()
        & (df['entity_type'] == 'INDIVIDUAL')
    )
    n = int(bad.sum())
    if not n:
        return 0

    occ = df.loc[bad, 'contributor_occupation'].fillna('').str.upper()
    is_status = occ.isin(STATUS_WORDS)
    df.loc[bad & is_status.reindex(df.index, fill_value=False), 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[bad & (~is_status).reindex(df.index, fill_value=False), 'occupation_status'] = 'EMPLOYER_MISSING'
    return n


def _fix_employed_no_category(df: pd.DataFrame) -> int:
    """E. occ='EMPLOYED' with no category → OTHER."""
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_occupation'] == 'EMPLOYED')
        & df['occupation_category'].isna()
    )
    n = int(mask.sum())
    if not n:
        return 0

    df.loc[mask, 'occupation_category'] = 'OTHER'
    emp_is_emp = mask & (df['contributor_employer'].fillna('').str.upper() == 'EMPLOYED')
    df.loc[emp_is_emp, 'contributor_employer'] = np.nan
    df.loc[emp_is_emp, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n


def _fix_status_word_in_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """G. EMPLOYER_MISSING/NOT_DISCLOSED + status word in occupation → NOT_APPLICABLE."""
    mask = (
        is_indiv
        & df['occupation_status'].isin(['NOT_DISCLOSED', 'EMPLOYER_MISSING'])
        & df['contributor_occupation'].isin(STATUS_WORDS)
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    return n


def _fix_not_disclosed_with_real_occ(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """H. NOT_DISCLOSED with real occupation → EMPLOYER_MISSING."""
    mask = (
        is_indiv
        & (df['occupation_status'] == 'NOT_DISCLOSED')
        & df['contributor_occupation'].notna()
        & ~df['contributor_occupation'].isin(STATUS_WORDS)
        & df['contributor_employer'].isna()
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_status'] = 'EMPLOYER_MISSING'
    return n


def _fill_null_occupation_category(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """I. occupation_category NULL → fill based on entity type."""
    n = 0
    null_indiv = is_indiv & df['occupation_category'].isna()
    if null_indiv.any():
        df.loc[null_indiv, 'occupation_category'] = 'OTHER'
        n += int(null_indiv.sum())

    null_comm = (df['entity_type'] == 'COMMITTEE/PAC') & df['occupation_category'].isna()
    if null_comm.any():
        df.loc[null_comm, 'occupation_category'] = 'POLITICAL COMMITTEE'
        n += int(null_comm.sum())
    return n


def _fix_bitton_edge_case(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """L. occ='NOT DISCLOSED' + emp='SELF-EMPLOYED' → contradictory; fix status."""
    mask = (
        is_indiv
        & (df['contributor_occupation'].fillna('').str.upper() == 'NOT DISCLOSED')
        & (df['contributor_employer'].fillna('').str.upper() == 'SELF-EMPLOYED')
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
        df.loc[mask, 'occupation_category'] = 'SELF-EMPLOYED'
    return n


def _fix_unknown_category(df: pd.DataFrame) -> int:
    """P. Fix UNKNOWN occupation_category → OTHER."""
    mask = df['occupation_category'] == 'UNKNOWN'
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n


def _fix_not_disclosed_in_other(df: pd.DataFrame) -> int:
    """AN. occupation='NOT DISCLOSED' with category=OTHER -> fix status."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    mask = (
        is_indiv
        & (df['contributor_occupation'] == 'NOT DISCLOSED')
        & (df['occupation_category'] == 'OTHER')
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'occupation_status'] = 'NOT_DISCLOSED'
        no_emp = mask & df['contributor_employer'].isna()
        if no_emp.any():
            df.loc[no_emp, 'occupation_category'] = np.nan
    return n


def _fix_web_artifact_occupation(df: pd.DataFrame) -> int:
    """AP. Web form artifacts in occupation field -> NaN.
    e.g. 'LOADING', 'ACMIO', 'REMD' -> clear junk."""
    _JUNK = {'LOADING', 'ACMIO', 'REMD', 'SWAP MEET'}
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & occ.isin(_JUNK)
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'OTHER'
    return n


def _fix_employed_as_occupation(df: pd.DataFrame) -> int:
    """AQ. occupation='EMPLOYED' -> derive real occupation from employer context."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    mask = is_indiv & (df['contributor_occupation'] == 'EMPLOYED')
    n = int(mask.sum())
    if not n:
        return 0

    # If employer is SELF-EMPLOYED, set occ to SELF-EMPLOYED
    se = mask & (df['contributor_employer'].fillna('') == 'SELF-EMPLOYED')
    if se.any():
        df.loc[se, 'contributor_occupation'] = 'SELF-EMPLOYED'
        df.loc[se, 'occupation_category'] = 'SELF-EMPLOYED'
        df.loc[se, 'occupation_status'] = 'NOT_APPLICABLE'

    # Otherwise just mark as OTHER (EMPLOYED is not informative)
    rest = mask & ~se
    if rest.any():
        df.loc[rest, 'occupation_category'] = 'OTHER'
    return n


def _fix_emp_occ_category_consistency(df: pd.DataFrame) -> int:
    """AR. Fix cross-field inconsistencies between employer, occupation, and category.

    Rules:
    1) emp=SELF-EMPLOYED → category should be SELF-EMPLOYED (unless occ has a real category)
    2) emp=RETIRED → occ should be RETIRED, category=RETIRED
    3) occ=RETIRED but emp is a real company → contradictory, trust the employer
    4) category=SELF-EMPLOYED but emp is a real company → fix category from occupation
    """
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    cat = df['occupation_category'].fillna('')
    n = 0

    # Rule 1: emp=RETIRED but category != RETIRED and occ is empty or RETIRED
    ret_wrong_cat = (
        is_indiv & (emp == 'RETIRED')
        & (cat != 'RETIRED')
        & (occ.isin({'RETIRED', ''}) | df['contributor_occupation'].isna())
    )
    n1 = int(ret_wrong_cat.sum())
    if n1:
        df.loc[ret_wrong_cat, 'contributor_occupation'] = 'RETIRED'
        df.loc[ret_wrong_cat, 'occupation_category'] = 'RETIRED'
        df.loc[ret_wrong_cat, 'occupation_status'] = 'NOT_APPLICABLE'
        n += n1

    # Rule 2: emp=NOT EMPLOYED but category != NOT EMPLOYED
    ne_wrong_cat = (
        is_indiv & (emp == 'NOT EMPLOYED')
        & (cat != 'NOT EMPLOYED')
        & (occ.isin({'NOT EMPLOYED', 'UNEMPLOYED', ''}) | df['contributor_occupation'].isna())
    )
    n2 = int(ne_wrong_cat.sum())
    if n2:
        df.loc[ne_wrong_cat, 'occupation_category'] = 'NOT EMPLOYED'
        n += n2

    # Rule 3: emp=HOMEMAKER but category != HOMEMAKER
    hm_wrong_cat = (
        is_indiv & (emp == 'HOMEMAKER')
        & (cat != 'HOMEMAKER')
        & (occ.isin({'HOMEMAKER', 'HOUSEWIFE', ''}) | df['contributor_occupation'].isna())
    )
    n3 = int(hm_wrong_cat.sum())
    if n3:
        df.loc[hm_wrong_cat, 'occupation_category'] = 'HOMEMAKER'
        n += n3

    # Rule 4: category=SELF-EMPLOYED but emp is a real company (not in SKIP)
    se_cat_real_emp = (
        is_indiv & (cat == 'SELF-EMPLOYED')
        & ~emp.isin(SKIP_EMPLOYERS) & (emp != '')
    )
    if se_cat_real_emp.any():
        real_occ = df.loc[se_cat_real_emp, 'contributor_occupation'].fillna('')
        new_cats = _categorize(real_occ)
        needs_fix = se_cat_real_emp & (new_cats != 'SELF-EMPLOYED') & new_cats.notna()
        n4 = int(needs_fix.sum())
        if n4:
            df.loc[needs_fix, 'occupation_category'] = new_cats[needs_fix]
            df.loc[needs_fix, 'occupation_status'] = 'DISCLOSED'
            n += n4

    return n


def _fix_slash_occupation(df: pd.DataFrame) -> int:
    """AS. Occupation with slash (INVESTOR/DEVELOPER) → take the first part for categorization.
    Only when occupation_category is OTHER or NULL — don't override good categorizations."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    cat = df['occupation_category'].fillna('')

    has_slash = is_indiv & occ.str.contains('/', na=False) & (cat.isin({'OTHER', ''}) | df['occupation_category'].isna())
    n = int(has_slash.sum())
    if not n:
        return 0

    # Take the first part before the slash
    first_part = occ[has_slash].str.split('/').str[0].str.strip()
    new_cats = _categorize(first_part)
    improved = has_slash & new_cats.notna() & (new_cats != 'OTHER')
    n_fixed = int(improved.sum())
    if n_fixed:
        df.loc[improved, 'occupation_category'] = new_cats[improved]
    return n_fixed


def _reclassify_other_category(df: pd.DataFrame) -> int:
    """AT. Second pass: try to reclassify OTHER occupation_category using keyword matching.
    Catches occupations that have recognizable keywords but weren't in OCCUPATION_FIXES."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    is_other = is_indiv & (df['occupation_category'] == 'OTHER')
    occ = df.loc[is_other, 'contributor_occupation'].fillna('')

    if occ.empty:
        return 0

    # Keyword → category mapping for common patterns.
    # Every category here MUST exist in VALID_CATEGORIES (i.e. be a category
    # name used by CATEGORY_RULES) — otherwise this net writes a value the
    # quality gate then reports as invalid. Three names here had drifted
    # ('TECHNOLOGY / ENGINEERING', 'ARCHITECTURE / DESIGN',
    # 'NON-PROFIT / PHILANTHROPY') and went unnoticed because no row had
    # reached those branches yet. test_reclassify_categories_are_valid guards it.
    _KEYWORD_CATS = [
        (r'\bATTORNEY\b|\bLAWYER\b|\bLEGAL\b|\bCOUNSEL\b|\bESQ\b', 'LEGAL'),
        (r'\bDOCTOR\b|\bPHYSICIAN\b|\bSURGEON\b|\bMD\b|\bDENTIST\b|\bNURSE\b|\bRN\b|\bPHARMACIST\b|\bVETERINAR', 'MEDICAL / HEALTHCARE'),
        (r'\bENGINEER\b|\bSOFTWARE\b|\bDEVELOPER\b|\bPROGRAMMER\b|\bIT\b|\bTECH\b', 'TECHNOLOGY'),
        (r'\bPROFESSOR\b|\bTEACHER\b|\bEDUCATOR\b|\bPRINCIPAL\b|\bDEAN\b|\bACADEMIC\b', 'EDUCATION'),
        (r'\bREAL ESTATE\b|\bREALTOR\b|\bBROKER\b|\bPROPERT', 'REAL ESTATE'),
        (r'\bACCOUNTANT\b|\bCPA\b|\bAUDITOR\b|\bBOOKKEEPER\b', 'FINANCE / INVESTMENT'),
        (r'\bBANKER\b|\bINVEST\b|\bFINANC\b|\bTRADER\b|\bANALYST\b|\bHEDGE\b|\bPORTFOLIO\b', 'FINANCE / INVESTMENT'),
        (r'\bCONSULTANT\b|\bADVISOR\b', 'CONSULTING'),
        (r'\bRABBI\b|\bPASTOR\b|\bMINISTER\b|\bCLERGY\b|\bPRIEST\b', 'RELIGIOUS'),
        (r'\bWRITER\b|\bAUTHOR\b|\bJOURNALIST\b|\bEDITOR\b|\bREPORTER\b|\bPUBLISH', 'ARTS / ENTERTAINMENT'),
        (r'\bARCHITECT\b', 'ARTS / ENTERTAINMENT'),
        (r'\bPSYCHOLOG\b|\bTHERAPIST\b|\bSOCIAL WORK\b|\bCOUNSELOR\b', 'MEDICAL / HEALTHCARE'),
        (r'\bSALES\b|\bMARKETING\b|\bADVERTIS', 'SALES / MARKETING'),
        (r'\bEXECUTIVE\b|\bCEO\b|\bCFO\b|\bCOO\b|\bPRESIDENT\b|\bDIRECTOR\b', 'EXECUTIVE / C-SUITE'),
        (r'\bPILOT\b|\bAVIAT', 'TRANSPORTATION'),
        (r'\bPHILANTHROP\b|\bNON.?PROFIT\b|\bNGO\b', 'NONPROFIT / PHILANTHROPY'),
    ]

    n = 0
    for pattern, category in _KEYWORD_CATS:
        matches = is_other & occ.str.contains(pattern, case=False, na=False)
        # Only reclassify records still in OTHER (avoid double-counting)
        still_other = matches & (df['occupation_category'] == 'OTHER')
        cnt = int(still_other.sum())
        if cnt:
            df.loc[still_other, 'occupation_category'] = category
            n += cnt

    return n
