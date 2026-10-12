"""Employer/occupation swaps and self-employment consistency."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS
from fec.cleaning.occupations.normalize import (
    categorize_occupation,
    categorize_final,
    swap_employer_and_occupation,
)
from fec.cleaning.safety_nets.own_name import had_legal_suffix, is_own_name
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    ROLE_AS_EMPLOYER,
    SELF_EMPLOYED_OCC_AS_EMP,
)

# HEALTH followed by person noun: job title
_HEALTH_JOB_TAIL = (
    r'\s+(?:CARE\s+)?(?:INDUSTRY\s+)?'
    r'(?:COUNSELOR|THERAPIST|CLINICIAN|PROFESSIONAL|COACH|AIDE|NURSE|WORKER'
    r'|PRACTITIONER|PROVIDER|CONSULTANT|ADVISOR|EDUCATOR'
    r'|ADMINISTRATOR|ADM|EXECUTIVE|EXEC|OWNER|INVESTOR|SPECIALIST|TECHNICIAN'
    r'|ASSISTANT|MANAGER|DIRECTOR|OFFICER|ANALYST|STRATEGIST)\b'
)

# company markers; non-capturing avoids pandas warning
COMPANY_NAME_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C\.?|L\.L\.P\.?|INC\.?|CORP\.?|CO\.?|'
    r'LTD\.?|LP|PLC|P\.?C\.?|P\.?A\.?|COMPANY|CORPORATION|'
    r'HOLDINGS|GROUP|PARTNERS|VENTURES|FUND|CAPITAL|'
    r'ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY|ADVISORS|'
    r'INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|'
    rf'HEALTH(?:CARE)?(?!{_HEALTH_JOB_TAIL})|SOLUTIONS|SERVICES|SYSTEMS|TECHNOLOGIES|'
    r'MANAGEMENT|FOUNDATION|UNIVERSITY|COLLEGE|HOSPITAL|CENTER|'
    r'INTERNATIONAL|GLOBAL|TRUST)\b'
    # or any X & Y name
    r'|\b\w+\s*&\s*\w+\b',
    re.IGNORECASE,
)

# tighter company markers for the occupation swap
COMPANY_SUFFIX_RE = re.compile(
    r'\b(?:LLC|INC|CORP|GROUP|PARTNERS|CAPITAL|REALTY|PROPERTIES|ADVISORS|HOLDINGS)\b',
    re.IGNORECASE,
)

_SKIP_OCC = SKIP_OCCUPATIONS | {'OWNER', 'CEO', 'PRESIDENT'}
KNOWN_OCCUPATIONS = (
    OCCUPATION_AS_EMPLOYER
    | ROLE_AS_EMPLOYER
    | JOB_TITLE_AS_EMPLOYER
    | SELF_EMPLOYED_OCC_AS_EMP
    | {"ADVOCATE"}
)

# organisation titles; alone they signal swapped boxes
ORG_TITLE_ROLES = frozenset({
    'CEO', 'COO', 'PRESIDENT', 'PRESIDENT CEO', 'VICE PRESIDENT', 'VP',
    'CHAIRMAN', 'FOUNDER', 'DEPUTY CEO', 'SENIOR DIRECTOR',
    'MANAGING DIRECTOR', 'MANAGING PARTNER', 'EXEC DIRECTOR',
})
# placeholders that categorize OTHER without naming anything
_NO_INFO_OCCUPATIONS = frozenset({'EMPLOYED', 'NOT DISCLOSED', 'OTHER'})


# filer name as written; no donor_key yet
def _filer_names(df: pd.DataFrame) -> pd.Series:
    if 'contributor_name' in df.columns:
        names = df['contributor_name']
    elif {'contributor_last_name', 'contributor_first_name'} <= set(df.columns):
        names = (df['contributor_last_name'].fillna('').astype(str) + ', '
                 + df['contributor_first_name'].fillna('').astype(str))
    else:
        names = pd.Series('', index=df.index)
    return names.fillna('').astype(str).str.strip().str.upper()


# one-filer uncategorized occupation is the organisation
def _occupation_names_no_job(df: pd.DataFrame, occ: pd.Series, candidates: pd.Series) -> pd.Series:
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
        & ~upper.isin(KNOWN_OCCUPATIONS)
        & ~upper.isin(SKIP_OCCUPATIONS)
        & ~upper.isin(NOT_REAL_EMPLOYER)
        & ~upper.isin(_NO_INFO_OCCUPATIONS)
    )


# occupation holds company: marker or org title
def occupation_holds_company(df: pd.DataFrame, occ: pd.Series, emp: pd.Series, candidates: pd.Series) -> pd.Series:
    marker = candidates & occ.str.contains(COMPANY_NAME_RE, na=False)
    org_title = candidates & emp.str.upper().isin(ORG_TITLE_ROLES)
    return marker | _occupation_names_no_job(df, occ, org_title)


# non-company employer matching occupation: SELF-EMPLOYED
def fix_employer_equals_occupation(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    looks_like_company = emp.str.contains(COMPANY_NAME_RE, na=False)
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


# occupation-word employer: swap or SELF-EMPLOYED
def fix_employer_is_occupation_word(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')

    mask = is_indiv & emp.isin(OCCUPATION_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    # retired: employer word was old occupation
    is_retired = mask & occ.isin({'RETIRED', 'NOT EMPLOYED'})
    if is_retired.any():
        old_emp = df.loc[is_retired, 'contributor_employer'].copy()
        df.loc[is_retired, 'contributor_occupation'] = old_emp
        df.loc[is_retired, 'contributor_employer'] = 'RETIRED'
        df.loc[is_retired, 'occupation_status'] = 'NOT_APPLICABLE'

    occ_empty_or_se = (
        mask & ~occ.isin({'RETIRED', 'NOT EMPLOYED'}) & occ.isin({'', 'SELF-EMPLOYED'})
    )
    if occ_empty_or_se.any():
        old_emp = df.loc[occ_empty_or_se, 'contributor_employer'].copy()
        df.loc[occ_empty_or_se, 'contributor_occupation'] = old_emp
        df.loc[occ_empty_or_se, 'occupation_category'] = categorize_occupation(old_emp)
        df.loc[occ_empty_or_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[occ_empty_or_se, 'occupation_status'] = 'DISCLOSED'

    # company in occupation means swapped fields
    other = mask & ~is_retired & ~occ_empty_or_se
    if other.any():
        occ_looks_like_company = occ.str.contains(COMPANY_NAME_RE, na=False)
        company_in_occ = other & occ_looks_like_company
        if company_in_occ.any():
            swap_employer_and_occupation(df, company_in_occ, status='DISCLOSED')
        not_company = other & ~occ_looks_like_company
        if not_company.any():
            df.loc[not_company, 'contributor_employer'] = 'SELF-EMPLOYED'

    return n_fixed


# swap known company back; before fix_role_as_employer
def swap_role_employer_with_known_company(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').astype(str).str.strip().str.upper()
    occ = df['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()

    titles = ROLE_AS_EMPLOYER | OCCUPATION_AS_EMPLOYER | JOB_TITLE_AS_EMPLOYER
    candidates = is_indiv & emp.isin(titles) & occ.ne('')
    if not candidates.any():
        return 0

    # company only if other donors file it
    real = set(emp[is_indiv & ~emp.isin(titles) & ~emp.isin(SKIP_EMPLOYERS) & emp.ne('')])
    if not real:
        return 0

    known_company = occ.replace(EMPLOYER_SYNONYMS)
    mask = candidates & (occ.isin(real) | known_company.isin(real))
    n_fixed = int(mask.sum())
    if n_fixed:
        swap_employer_and_occupation(df, mask, status='DISCLOSED')
    return n_fixed


# role employer: swap back or SELF-EMPLOYED
def fix_role_as_employer(df: pd.DataFrame) -> int:
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


# self-employed rows: own-name or occupation employer
def fix_self_employed_consistency(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    emp = df['contributor_employer'].fillna('')
    n_fixed = 0

    se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(SKIP_EMPLOYERS)

    if se_with_company.any():
        for idx in df.loc[se_with_company].index:
            if is_own_name(df, idx, emp.at[idx]) and not had_legal_suffix(df, idx):
                df.at[idx, 'contributor_employer'] = 'SELF-EMPLOYED'
                n_fixed += 1

        # refresh emp after own-name changes
        emp = df['contributor_employer'].fillna('')
        se_with_company = is_indiv & (occ == 'SELF-EMPLOYED') & ~emp.isin(SKIP_EMPLOYERS)

        emp_is_occ = se_with_company & emp.isin(SELF_EMPLOYED_OCC_AS_EMP)
        if emp_is_occ.any():
            old_emp = df.loc[emp_is_occ, 'contributor_employer'].copy()
            df.loc[emp_is_occ, 'contributor_occupation'] = old_emp
            df.loc[emp_is_occ, 'occupation_category'] = categorize_occupation(old_emp)
            df.loc[emp_is_occ, 'contributor_employer'] = 'SELF-EMPLOYED'
            df.loc[emp_is_occ, 'occupation_status'] = 'DISCLOSED'
            n_fixed += int(emp_is_occ.sum())

    return n_fixed


