"""Employer/occupation misplacement fixes: swapped fields and self-employment consistency."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.work.employer_aliases import EMPLOYER_SYNONYMS
from fec.cleaning.work.normalize import (
    _categorize,
    categorize_final,
    swap_employer_and_occupation,
)
from fec.cleaning.rules.own_name import _had_legal_suffix, _is_own_name
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP,
)

# HEALTH names a company unless a person noun follows.
_HEALTH_JOB_TAIL = (
    r'\s+(?:CARE\s+)?(?:INDUSTRY\s+)?'
    r'(?:COUNSELOR|THERAPIST|CLINICIAN|PROFESSIONAL|COACH|AIDE|NURSE|WORKER'
    r'|PRACTITIONER|PROVIDER|CONSULTANT|ADVISOR|EDUCATOR'
    r'|ADMINISTRATOR|ADM|EXECUTIVE|EXEC|OWNER|INVESTOR|SPECIALIST|TECHNICIAN'
    r'|ASSISTANT|MANAGER|DIRECTOR|OFFICER|ANALYST|STRATEGIST)\b'
)

# Markers of a real legal entity name, not an industry.
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

# Tighter marker set for the AL swap.
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
    | {"ADVOCATE", "SCULPTOR"}
)

# Organisation titles ("CEO of X") filed alone mean swapped boxes.
ORG_TITLE_ROLES = frozenset({
    'CEO', 'COO', 'PRESIDENT', 'PRESIDENT CEO', 'VICE PRESIDENT', 'VP',
    'CHAIRMAN', 'FOUNDER', 'DEPUTY CEO', 'SENIOR DIRECTOR',
    'MANAGING DIRECTOR', 'MANAGING PARTNER', 'EXEC DIRECTOR',
})
# placeholders that categorize OTHER without naming anything
_NO_INFO_OCCUPATIONS = frozenset({'EMPLOYED', 'NOT DISCLOSED', 'OTHER'})


# build each row's filer name as written on the filing
def _filer_names(df: pd.DataFrame) -> pd.Series:
    """The filer's name as written on each row."""
    if 'contributor_name' in df.columns:
        names = df['contributor_name']
    elif {'contributor_last_name', 'contributor_first_name'} <= set(df.columns):
        names = (df['contributor_last_name'].fillna('').astype(str) + ', '
                 + df['contributor_first_name'].fillna('').astype(str))
    else:
        names = pd.Series('', index=df.index)
    return names.fillna('').astype(str).str.strip().str.upper()


# find candidates whose occupation text is really an organization name
def _occupation_names_no_job(df: pd.DataFrame, occ: pd.Series, candidates: pd.Series) -> pd.Series:
    """Rows whose occupation text is no job, so an organisation."""
    if not candidates.any():
        return candidates
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    names = _filer_names(df)
    filed = is_indiv & occ.ne('')
    filers = names[filed].groupby(occ[filed]).nunique()
    only_on_candidates = ~occ.isin(set(occ[filed & ~candidates]))
    one_filer = occ.map(filers).fillna(0).eq(1)

    uncategorised = pd.Series(False, index=occ.index)
    cand_occ = occ[candidates]
    uncategorised.loc[cand_occ.index] = categorize_final(cand_occ).eq('OTHER').to_numpy()
    upper = occ.str.upper()
    return (
        candidates
        & occ.ne('')
        & uncategorised
        & only_on_candidates
        & one_filer
        & ~upper.isin(_KNOWN_OCCUPATIONS)
        & ~upper.isin(SKIP_OCCUPATIONS)
        & ~upper.isin(NOT_REAL_EMPLOYER)
        & ~upper.isin(_NO_INFO_OCCUPATIONS)
    )


# check whether the occupation box actually holds the company
def occupation_holds_company(df: pd.DataFrame, occ: pd.Series, emp: pd.Series, candidates: pd.Series) -> pd.Series:
    """A role in the employer box: is occupation the company?"""
    marker = candidates & occ.str.contains(_COMPANY_NAME_RE, na=False)
    org_title = candidates & emp.str.upper().isin(ORG_TITLE_ROLES)
    return marker | _occupation_names_no_job(df, occ, org_title)


# set employer to SELF-EMPLOYED when it duplicates the occupation
def _fix_employer_equals_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """Employer equals occupation: SELF-EMPLOYED, except real companies."""
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


# fix employer field that actually holds an occupation word
def _fix_employer_is_occupation_word(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """Employer is an occupation word: swap or set SELF-EMPLOYED."""
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(OCCUPATION_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    # Case A: occupation RETIRED, so swap.
    is_retired = mask & occ.isin({'RETIRED', 'NOT EMPLOYED'})
    if is_retired.any():
        old_emp = df.loc[is_retired, 'contributor_employer'].copy()
        df.loc[is_retired, 'contributor_occupation'] = old_emp
        df.loc[is_retired, 'contributor_employer'] = 'RETIRED'
        df.loc[is_retired, 'occupation_status'] = 'NOT_APPLICABLE'

    # Case B: occupation empty or SELF-EMPLOYED, so move the word.
    occ_empty_or_se = mask & occ.isin({'', 'SELF-EMPLOYED'})
    if occ_empty_or_se.any():
        old_emp = df.loc[occ_empty_or_se, 'contributor_employer'].copy()
        df.loc[occ_empty_or_se, 'contributor_occupation'] = old_emp
        df.loc[occ_empty_or_se, 'occupation_category'] = _categorize(old_emp)
        df.loc[occ_empty_or_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[occ_empty_or_se, 'occupation_status'] = 'DISCLOSED'

    # Case C: a company in occupation means swapped fields.
    other = mask & ~is_retired & ~occ_empty_or_se
    if other.any():
        occ_looks_like_company = occ.str.contains(_COMPANY_NAME_RE, na=False)
        company_in_occ = other & occ_looks_like_company
        if company_in_occ.any():
            swap_employer_and_occupation(df, company_in_occ, status='DISCLOSED')
        not_company = other & ~occ_looks_like_company
        if not_company.any():
            df.loc[not_company, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n_fixed


# swap role in employer back when occupation holds a company
def _swap_role_employer_with_known_company(df: pd.DataFrame) -> int:
    """Title in employer, company in occupation; runs before AD."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()

    titles = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER
    candidates = is_indiv & emp.isin(titles) & occ.ne('')
    if not candidates.any():
        return 0

    # Occupation is a company only when other donors' employer matches.
    real = set(emp[is_indiv & ~emp.isin(titles) & ~emp.isin(SKIP_EMPLOYERS) & emp.ne('')])
    if not real:
        return 0

    known_company = occ.replace(EMPLOYER_SYNONYMS)
    mask = candidates & (occ.isin(real) | known_company.isin(real))
    n_fixed = int(mask.sum())
    if n_fixed:
        swap_employer_and_occupation(df, mask, status='DISCLOSED')
    return n_fixed


# swap back or self-employ rows with a role/title as employer
def _fix_role_as_employer(df: pd.DataFrame) -> int:
    """Title in employer: swap back if occupation holds the company."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(ROLE_AS_EMPLOYER) & ~occ.isin(_SKIP_OCC) & occ.ne('')
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    swap = occupation_holds_company(df, occ, emp, mask)
    if swap.any():
        swap_employer_and_occupation(df, swap, status='DISCLOSED')

    self_emp = mask & ~swap
    if self_emp.any():
        df.loc[self_emp, 'contributor_employer'] = 'SELF-EMPLOYED'
    return n_fixed


# clean self-employed rows whose employer is really an occupation word
def _fix_self_employed_consistency(df: pd.DataFrame) -> int:
    """SELF-EMPLOYED occupation: own-name employer or occupation word moved."""
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


