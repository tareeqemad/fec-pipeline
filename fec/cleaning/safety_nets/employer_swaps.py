"""Employer/occupation misplacement fixes: swapped fields and self-employment consistency."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.occupations import _categorize
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS
from fec.config.constants import (
    SKIP_EMPLOYERS, SKIP_OCCUPATIONS, OCCUPATION_AS_EMPLOYER, ROLE_AS_EMPLOYER,
    JOB_TITLE_AS_EMPLOYER, SELF_EMPLOYED_OCC_AS_EMP, LEGAL_SUFFIX_RE,
)
from fec.config.occupation_rules.rules import (
    KNOWN_COMPANY_OCCUPATIONS,
    OCCUPATION_CANONICAL,
)

# HEALTH / HEALTHCARE name a company (SUMMIT HEALTH, CVS HEALTH) but not when
# a person noun follows: MENTAL HEALTH COUNSELOR, HEALTH COACH, HEALTHCARE
# EXECUTIVE are job titles. Without this, rule AD swapped raw OWN BUSINESS /
# MENTAL HEALTH COUNSELOR into employer=MENTAL HEALTH COUNSELOR. ADVOCATE is
# left out: HEALTH ADVOCATE is also a company (Health Advocate, Inc.).
_HEALTH_JOB_TAIL = (
    r'\s+(?:CARE\s+)?(?:INDUSTRY\s+)?'
    r'(?:COUNSELOR|THERAPIST|CLINICIAN|PROFESSIONAL|COACH|AIDE|NURSE|WORKER'
    r'|PRACTITIONER|PROVIDER|CONSULTANT|ADVISOR|EDUCATOR'
    r'|ADMINISTRATOR|ADM|EXECUTIVE|EXEC|OWNER|INVESTOR|SPECIALIST|TECHNICIAN'
    r'|ASSISTANT|MANAGER|DIRECTOR|OFFICER|ANALYST|STRATEGIST)\b'
)

# Markers that a string is a real legal entity name, not an industry word.
# Non-capturing group avoids the pandas regex-with-group warning.
_COMPANY_NAME_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C\.?|L\.L\.P\.?|INC\.?|CORP\.?|CO\.?|'
    r'LTD\.?|LP|PLC|P\.?C\.?|P\.?A\.?|COMPANY|CORPORATION|'
    r'HOLDINGS|GROUP|PARTNERS|VENTURES|FUND|CAPITAL|'
    r'ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY|ADVISORS|'
    r'INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|'
    rf'HEALTH(?:CARE)?(?!{_HEALTH_JOB_TAIL})|SOLUTIONS|SERVICES|SYSTEMS|TECHNOLOGIES|'
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
_KNOWN_OCCUPATIONS = (
    OCCUPATION_AS_EMPLOYER
    | ROLE_AS_EMPLOYER
    | JOB_TITLE_AS_EMPLOYER
    | SELF_EMPLOYED_OCC_AS_EMP
    | {"ADVOCATE"}
)

def _swap_occ_emp_fields(df: pd.DataFrame, mask: pd.Series, *, status=None) -> None:
    """Swap contributor_employer <-> contributor_occupation where mask is True, optionally setting occupation_status."""
    old_emp = df.loc[mask, 'contributor_employer'].copy()
    old_occ = df.loc[mask, 'contributor_occupation'].copy()
    df.loc[mask, 'contributor_employer'] = old_occ
    new_occ = old_emp.replace(OCCUPATION_CANONICAL)
    df.loc[mask, 'contributor_occupation'] = new_occ
    df.loc[mask, 'occupation_category'] = _categorize(new_occ)
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

    known_company = occ.replace(EMPLOYER_SYNONYMS)
    mask = candidates & (occ.isin(real) | known_company.isin(real))
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
        for idx in df.loc[se_with_company].index:
            if _is_own_name(df, idx, emp.at[idx]) and not _had_legal_suffix(df, idx):
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


def _is_own_name(df: pd.DataFrame, idx, employer: str) -> bool:
    """True only when the whole employer value is the donor's name."""
    first = _name_word_set(df.at[idx, 'contributor_first_name'])
    last = _name_word_set(df.at[idx, 'contributor_last_name'])
    if not first or not last:
        return False

    possible = {first | last}
    if 'contributor_middle_name' in df.columns:
        middle = _name_word_set(df.at[idx, 'contributor_middle_name'])
        if middle:
            possible.add(first | middle | last)
    return _name_word_set(employer) in possible


def _had_legal_suffix(df: pd.DataFrame, idx) -> bool:
    """The raw suffix proves an own-named value is a company, not a bare name."""
    if 'contributor_employer_original' not in df.columns:
        return False
    original = str(df.at[idx, 'contributor_employer_original']).strip().upper()
    return bool(LEGAL_SUFFIX_RE.search(original))


def _fix_own_name_as_employer(df: pd.DataFrame) -> int:
    """AE2. Employer is the donor's own FULL name -> SELF-EMPLOYED; a shared surname alone is often a real firm, so both names required."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    hits = []
    for idx in df.index[mask]:
        if _is_own_name(df, idx, emp.at[idx]) and not _had_legal_suffix(df, idx):
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

    se_mask = (
        is_indiv
        & (emp == 'SELF-EMPLOYED')
        & (occ != '')
        & ~occ.isin(SKIP_OCCUPATIONS)
        & ~occ.isin(_KNOWN_OCCUPATIONS)
    )

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
    """AO. Swap a curated company from occupation only when employer is a job."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')

    # Two company names are not a safe swap. That case needs a per-row
    # override because neither field tells us the person's actual role.
    emp_is_company = emp.str.contains(_COMPANY_NAME_RE, na=False)
    mask = is_indiv & occ.isin(KNOWN_COMPANY_OCCUPATIONS) & emp.ne('') & ~emp_is_company
    n_fixed = int(mask.sum())
    if n_fixed:
        _swap_occ_emp_fields(df, mask)
    return n_fixed
