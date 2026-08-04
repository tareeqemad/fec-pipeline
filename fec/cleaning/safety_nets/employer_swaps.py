"""Employer/occupation misplacement fixes: swapped fields and self-employment consistency."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.occupations import _categorize
from fec.config.constants import (
    SKIP_EMPLOYERS, SKIP_OCCUPATIONS, OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER,
    JOB_TITLE_AS_EMPLOYER, SELF_EMPLOYED_OCC_AS_EMP,
)
from fec.config.occupation_rules import KNOWN_COMPANY_OCCUPATIONS

# Markers that a string is a real legal entity name, not an industry word.
# Non-capturing group avoids the pandas regex-with-group warning.
_COMPANY_NAME_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C\.?|L\.L\.P\.?|INC\.?|CORP\.?|CO\.?|'
    r'LTD\.?|LP|PLC|P\.?C\.?|P\.?A\.?|COMPANY|CORPORATION|'
    r'HOLDINGS|GROUP|PARTNERS|VENTURES|FUND|CAPITAL|'
    r'ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY|ADVISORS|'
    r'INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|'
    r'HEALTH|HEALTHCARE|SOLUTIONS|SERVICES|SYSTEMS|TECHNOLOGIES|'
    r'MANAGEMENT|FOUNDATION|UNIVERSITY|COLLEGE|HOSPITAL|CENTER|'
    r'INTERNATIONAL|GLOBAL|TRUST)\b'
    # OR any ampersand between two words (BROWN & BROWN):
    r'|\b\w+\s*&\s*\w+\b',
    re.IGNORECASE,
)

# tighter marker set for the AL swap (occ holds the company)
_COMPANY_SUFFIX_RE = re.compile(
    r'\b(?:LLC|INC|CORP|GROUP|PARTNERS|CAPITAL|REALTY|PROPERTIES|ADVISORS|HOLDINGS)\b',
    re.IGNORECASE,
)

_SKIP_OCC = SKIP_OCCUPATIONS | {'OWNER', 'CEO', 'PRESIDENT'}

def _swap_occ_emp_fields(df: pd.DataFrame, mask: pd.Series, *, status=None) -> None:
    """Swap contributor_employer <-> contributor_occupation where mask is True, optionally setting occupation_status."""
    old_emp = df.loc[mask, 'contributor_employer'].copy()
    old_occ = df.loc[mask, 'contributor_occupation'].copy()
    df.loc[mask, 'contributor_employer'] = old_occ
    df.loc[mask, 'contributor_occupation'] = old_emp
    if status is not None:
        df.loc[mask, 'occupation_status'] = status


def _fix_employer_equals_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """U. Employer == occupation -> SELF-EMPLOYED, except real company names (donor works there, wrote it twice)."""
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    looks_like_company = emp.str.contains(_COMPANY_NAME_RE, na=False)
    mask = (
        is_indiv
        & (emp == occ)
        & emp.ne('')
        & ~emp.isin(SKIP_EMPLOYERS)
        & ~looks_like_company
    )
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n_fixed


def _fix_employer_is_occupation_word(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """V. Employer is an occupation word: swap with occupation or set SELF-EMPLOYED."""
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(OCCUPATION_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    # Case A: occ is RETIRED -> swap (emp was their old occupation)
    is_retired = mask & occ.isin({'RETIRED', 'NOT EMPLOYED'})
    if is_retired.any():
        old_emp = df.loc[is_retired, 'contributor_employer'].copy()
        df.loc[is_retired, 'contributor_occupation'] = old_emp
        df.loc[is_retired, 'contributor_employer'] = 'RETIRED'
        df.loc[is_retired, 'occupation_status'] = 'NOT_APPLICABLE'

    # Case B: occ empty or SELF-EMPLOYED -> move word to occ, emp=SELF-EMPLOYED
    occ_empty_or_se = (
        mask & ~occ.isin({'RETIRED', 'NOT EMPLOYED'}) & occ.isin({'', 'SELF-EMPLOYED'})
    )
    if occ_empty_or_se.any():
        old_emp = df.loc[occ_empty_or_se, 'contributor_employer'].copy()
        df.loc[occ_empty_or_se, 'contributor_occupation'] = old_emp
        df.loc[occ_empty_or_se, 'occupation_category'] = _categorize(old_emp)
        df.loc[occ_empty_or_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[occ_empty_or_se, 'occupation_status'] = 'DISCLOSED'

    # Case C: occ holds a different real value. A company name there means the
    # filer swapped the fields -- swap back rather than destroy it.
    other = mask & ~is_retired & ~occ_empty_or_se
    if other.any():
        occ_looks_like_company = occ.str.contains(_COMPANY_NAME_RE, na=False)
        company_in_occ = other & occ_looks_like_company
        if company_in_occ.any():
            _swap_occ_emp_fields(df, company_in_occ, status='DISCLOSED')
        not_company = other & ~occ_looks_like_company
        if not_company.any():
            df.loc[not_company, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n_fixed


def _swap_role_employer_with_known_company(df: pd.DataFrame) -> int:
    """AD0. Emp holds a title while occ holds the company; MUST run before AD, whose SELF-EMPLOYED collapse would strand the company."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()

    titles = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER
    candidates = is_indiv & emp.isin(titles) & occ.ne('')
    if not candidates.any():
        return 0

    # Occ counts as a company only when OTHER donors use that exact string as
    # their employer; candidate rows are excluded so a title never vouches for itself.
    real = set(emp[is_indiv & ~emp.isin(titles) & ~emp.isin(SKIP_EMPLOYERS) & emp.ne('')])
    if not real:
        return 0

    mask = candidates & occ.isin(real)
    n_fixed = int(mask.sum())
    if n_fixed:
        _swap_occ_emp_fields(df, mask, status='DISCLOSED')
        if 'employer_name_normalized' in df.columns:
            df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n_fixed


def _fix_role_as_employer(df: pd.DataFrame) -> int:
    """AD. Role/title in employer field: swap back if occ is a real company, else SELF-EMPLOYED."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(ROLE_AS_EMPLOYER) & ~occ.isin(_SKIP_OCC) & occ.ne('')
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    occ_looks_like_company = occ.str.contains(_COMPANY_NAME_RE, na=False)
    swap = mask & occ_looks_like_company
    if swap.any():
        _swap_occ_emp_fields(df, swap, status='DISCLOSED')

    self_emp = mask & ~occ_looks_like_company
    if self_emp.any():
        df.loc[self_emp, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n_fixed


def _fix_self_employed_consistency(df: pd.DataFrame) -> int:
    """AE. occ='SELF-EMPLOYED' rows: own-name employer -> SELF-EMPLOYED, occupation-word employer moved to occ."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')
    n_fixed = 0

    se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(SKIP_EMPLOYERS)

    if se_with_company.any():
        # Pattern 1: employer is the person's own name
        first_names = df['contributor_first_name'].fillna('').str.upper()
        last_names = df['contributor_last_name'].fillna('').str.upper()
        emp_upper = emp.str.upper()

        for idx in df.loc[se_with_company].index:
            first = first_names.at[idx].strip()
            last = last_names.at[idx].strip()
            employer = emp_upper.at[idx].strip()
            if not first or not last or not employer:
                continue
            emp_words = set(employer.replace(',', ' ').split())
            if {first, last}.issubset(emp_words):
                df.at[idx, 'contributor_employer'] = 'SELF-EMPLOYED'
                n_fixed += 1

        # refresh emp after pattern 1 changes
        emp = df['contributor_employer'].fillna('')
        se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(SKIP_EMPLOYERS)

        # Pattern 2: employer is an occupation word
        emp_is_occ = se_with_company & emp.isin(SELF_EMPLOYED_OCC_AS_EMP)
        if emp_is_occ.any():
            old_emp = df.loc[emp_is_occ, 'contributor_employer'].copy()
            df.loc[emp_is_occ, 'contributor_occupation'] = old_emp
            df.loc[emp_is_occ, 'occupation_category'] = _categorize(old_emp)
            df.loc[emp_is_occ, 'contributor_employer'] = 'SELF-EMPLOYED'
            df.loc[emp_is_occ, 'occupation_status'] = 'DISCLOSED'
            n_fixed += int(emp_is_occ.sum())

    return n_fixed


def _name_word_set(*parts: str) -> frozenset[str]:
    """Word-set of a personal name: punctuation dropped, single letters ignored, order ignored (FEC stores LAST, FIRST)."""
    words = re.sub(r'[^A-Z]', ' ', ' '.join(parts).upper()).split()
    return frozenset(word for word in words if len(word) > 1)


def _fix_own_name_as_employer(df: pd.DataFrame) -> int:
    """AE2. Employer is the donor's own FULL name -> SELF-EMPLOYED; a shared surname alone is often a real firm, so both names required."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    first = df['contributor_first_name'].fillna('')
    last = df['contributor_last_name'].fillna('')
    has_mid = 'contributor_middle_name' in df.columns
    mid = df['contributor_middle_name'].fillna('') if has_mid else None

    hits = []
    for idx in df.index[mask]:
        first_words = _name_word_set(first.at[idx])
        last_words = _name_word_set(last.at[idx])
        if not first_words or not last_words:
            continue  # need the full name to be safe
        own = {first_words | last_words}
        if has_mid:
            mid_words = _name_word_set(mid.at[idx])
            if mid_words:
                own.add(first_words | mid_words | last_words)
        if _name_word_set(emp.at[idx]) in own:
            hits.append(idx)

    if hits:
        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'
        if 'employer_name_normalized' in df.columns:
            df.loc[hits, 'employer_name_normalized'] = pd.NA
    return len(hits)


def _fix_company_name_as_occupation(df: pd.DataFrame) -> int:
    """AK. emp='SELF-EMPLOYED' but occ is a frequent employer name in the dataset -> occ is the real employer, swap."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    se_mask = is_indiv & (emp == 'SELF-EMPLOYED') & (occ != '')

    emp_counts = df.loc[is_indiv, 'contributor_employer'].value_counts()
    known_employers = set(emp_counts[emp_counts >= 10].index)

    occ_is_company = se_mask & occ.isin(known_employers)
    n_fixed = int(occ_is_company.sum())
    if not n_fixed:
        return 0

    # replacement occ comes from what other employees of that company report
    for occ_val in df.loc[occ_is_company, 'contributor_occupation'].unique():
        other_emps = df[(df['contributor_employer'] == occ_val) & is_indiv]
        real_occs = other_emps['contributor_occupation'].dropna()
        real_occs = real_occs[~real_occs.isin({'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', ''})]
        best_occ = real_occs.value_counts().index[0] if len(real_occs) > 0 else None

        mask = occ_is_company & (occ == occ_val)
        df.loc[mask, 'contributor_employer'] = occ_val
        if best_occ:
            df.loc[mask, 'contributor_occupation'] = best_occ
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'

    return n_fixed


def _fix_swapped_emp_occ_company(df: pd.DataFrame) -> int:
    """AL. emp=job title, occ=company name -> swap them."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')

    emp_is_title = is_indiv & emp.isin(JOB_TITLE_AS_EMPLOYER)
    occ_has_company = occ.str.contains(_COMPANY_SUFFIX_RE, na=False)
    mask = emp_is_title & occ_has_company
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    _swap_occ_emp_fields(df, mask)
    return n_fixed


def _fix_occ_emp_both_swapped(df: pd.DataFrame) -> int:
    """AO. Known swapped pairs where occ is a company and emp a job description -> swap."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    mask = is_indiv & occ.isin(KNOWN_COMPANY_OCCUPATIONS) & (emp != '')
    n_fixed = int(mask.sum())
    if n_fixed:
        _swap_occ_emp_fields(df, mask)
    return n_fixed
