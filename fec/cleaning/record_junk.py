"""Clean junk occupation and employer values."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import individual_index, upper_text, set_missing_occupation
from fec.cleaning.occupations.normalize import categorize_occupation
from fec.config.constants import JUNK_EMPLOYER_RE
from fec.config.occupation_rules.rules import (
    FINAL_JUNK_EMPLOYERS,
    FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS,
    OCCUPATION_KEYWORDS,
)

# input already uppercase; case-sensitive on purpose
_SCHOOL_EMP_RE = re.compile(
    r'SCHOOL|ACADEMY|COLLEGE|UNIVERSITY|EDUCATION|MDCPS|ISD\b|UNIFIED|DISTRICT',
)

# anchored so SELFRIDGES does not match
_SELF_EMP_RE = re.compile(r'^SELF(?:[\s\-/]*EMP(?:LOY\w*)?)?$')

# SELF followed by a tail
_SELF_PREFIX_RE = re.compile(r'^SELF[\s,/\-]+', re.IGNORECASE)
# SELF or SELF EMPLOYED prefix to strip
_SELF_PREFIX_STRIP_RE = re.compile(r'^SELF[\s,/\-]+(?:EMPLOYED[\s,/\-]*)?')
# company-type word means a real firm tail
_SELF_COMPANY_TAIL_RE = re.compile(
    r'\b(?:LLC|LLP|PLLC|INC|CORP|LTD|PC|PA|CAPITAL|REALTY|REAL ESTATE|'
    r'STRATEGIES|LAW|LAW OFFICES?|TRADING CO)\b',
)
_SELF_COMPANY_OVERRIDES = {
    'SELF EMPLOYED- STANDARDIZED SUCCESS, L': 'STANDARDIZED SUCCESS LLC',
    'SELF EMPLOYED AND TOTAL REALTY': 'TOTAL REALTY',
    'SELF AND STEVENSON UNIVERSITY': 'STEVENSON UNIVERSITY',
}
_GENERIC_SELF_TAILS = {'LAW OFFICE', 'COMPANY OWNER', 'PRIVATE CONTRACTOR', 'CONTRACTOR'}

# occupation ending in a number: "TEACHER 12345"
_OCC_NUM_TAIL_RE = re.compile(r'^[A-Z ]+\s+\d+$')
_TRAILING_NUM_RE = re.compile(r'\s+\d+$')

# run all final junk passes
def clean_remaining_junk(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    indiv_idx = individual_index(df)
    n_fixed = 0
    n_fixed += _clean_junk_employer_xxx(df, indiv_idx)
    n_fixed += _clean_junk_employer_patterns(df, indiv_idx)
    n_fixed += _clean_junk_short_occ(df, indiv_idx)
    n_fixed += _clean_junk_occ_patterns(df, indiv_idx)
    n_fixed += _clean_junk_number_tail(df, indiv_idx)
    n_fixed += _clean_junk_llp_standalone(df, indiv_idx)
    n_fixed += _clean_junk_not_disclosed(df, indiv_idx)
    n_fixed += _clean_junk_employer_fixes(df, indiv_idx)
    n_fixed += _clean_junk_nist(df, indiv_idx)
    return df, n_fixed


# blank employer values that are just x's
def _clean_junk_employer_xxx(df: pd.DataFrame, ii: pd.Index) -> int:
    emp = upper_text(df.loc[ii, 'contributor_employer'])
    xxx = ii[emp.isin({'XXX', 'XX', 'XXXX', 'X'})]
    if len(xxx):
        df.loc[xxx, 'contributor_employer'] = np.nan
        return len(xxx)
    return 0


# blank employers that are dates or numbers
def _clean_junk_employer_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    emp = upper_text(df.loc[ii, 'contributor_employer'])
    junk = ii[(emp != '') & emp.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        df.loc[junk, 'contributor_employer'] = np.nan
        return len(junk)
    return 0


# blank occupations too short to be real
def _clean_junk_short_occ(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = upper_text(df.loc[ii, 'contributor_occupation'])
    short = ii[(occ.str.len() <= 2) & (occ != '') & ~occ.isin(FINAL_SHORT_OCCUPATIONS)]
    if len(short):
        set_missing_occupation(df, short)
        return len(short)
    return 0


# blank junk occupations as MISSING for refill
def _clean_junk_occ_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = upper_text(df.loc[ii, 'contributor_occupation'])
    junk = ii[(occ != '') & occ.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        set_missing_occupation(df, junk)
        return len(junk)
    return 0


# strip a trailing number from an occupation
def _clean_junk_number_tail(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = upper_text(df.loc[ii, 'contributor_occupation'])
    num_tail = ii[occ.str.contains(_OCC_NUM_TAIL_RE, na=False)]
    if len(num_tail):
        df.loc[num_tail, 'contributor_occupation'] = occ[num_tail].str.replace(_TRAILING_NUM_RE, '', regex=True)
        return len(num_tail)
    return 0


# bare LLP occupation becomes attorney, legal category
def _clean_junk_llp_standalone(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = upper_text(df.loc[ii, 'contributor_occupation'])
    llp = ii[occ == 'LLP']
    if len(llp):
        df.loc[llp, 'contributor_occupation'] = 'ATTORNEY'
        df.loc[llp, 'occupation_category'] = 'LEGAL'
        df.loc[llp, 'occupation_status'] = 'DISCLOSED'
        return len(llp)
    return 0


# NOT DISCLOSED occupation text becomes missing
def _clean_junk_not_disclosed(df: pd.DataFrame, ii: pd.Index) -> int:
    nd_text = ii[
        (upper_text(df.loc[ii, 'contributor_occupation']) == 'NOT DISCLOSED')
        & (df.loc[ii, 'occupation_status'] != 'NOT_DISCLOSED')
    ]
    if len(nd_text):
        set_missing_occupation(df, nd_text)
        return len(nd_text)
    return 0


# fix known employer swaps and junk patterns
def _clean_junk_employer_fixes(df: pd.DataFrame, ii: pd.Index) -> int:
    n_fixed = 0
    emp = upper_text(df.loc[ii, 'contributor_employer'])
    occ = upper_text(df.loc[ii, 'contributor_occupation'])

    # DOCTOR employer, medical occupation: self-employed
    doc_emp = ii[emp.isin({'DOCTOR', 'PHYSICIAN'}) & occ.isin({'PHYSICIAN', 'DOCTOR', 'MEDICAL DOCTOR'})]
    if len(doc_emp):
        df.loc[doc_emp, 'contributor_employer'] = 'SELF-EMPLOYED'
        n_fixed += len(doc_emp)

    # occupation filed as employer: swap them
    swap_se = ii[emp.isin(OCCUPATION_KEYWORDS) & (occ == 'SELF-EMPLOYED')]
    if len(swap_se):
        real_occ = df.loc[swap_se, 'contributor_employer'].copy()
        df.loc[swap_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[swap_se, 'contributor_occupation'] = real_occ
        df.loc[swap_se, 'occupation_category'] = categorize_occupation(df.loc[swap_se, 'contributor_occupation'])
        df.loc[swap_se, 'occupation_status'] = 'DISCLOSED'
        n_fixed += len(swap_se)

    emp_junk = ii[emp.isin(FINAL_JUNK_EMPLOYERS)]
    if len(emp_junk):
        df.loc[emp_junk, 'contributor_employer'] = np.nan
        n_fixed += len(emp_junk)

    # SELF with a tail: firm or SELF-EMPLOYED
    emp_raw = df.loc[ii, 'contributor_employer'].fillna('')
    self_pattern = ii[
        emp_raw.str.match(_SELF_PREFIX_RE, na=False)
        & ~emp_raw.str.startswith('SELF-EMPLOYED', na=False)
    ]
    if len(self_pattern):
        extracted = emp_raw[self_pattern].str.replace(
            _SELF_PREFIX_STRIP_RE, '', regex=True
        ).str.strip().str.lstrip(',').str.lstrip('-').str.lstrip('/').str.strip()
        for idx, tail in extracted.items():
            original = emp_raw.at[idx].strip().upper()
            company = _SELF_COMPANY_OVERRIDES.get(original)
            clean_tail = tail.strip('() ').strip()
            if (not company and clean_tail not in _GENERIC_SELF_TAILS
                    and _SELF_COMPANY_TAIL_RE.search(clean_tail)):
                company = clean_tail
            df.at[idx, 'contributor_employer'] = company or 'SELF-EMPLOYED'
            n_fixed += 1

    return n_fixed


# NIST occupation: TEACHER at schools, else missing
def _clean_junk_nist(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = upper_text(df.loc[ii, 'contributor_occupation'])
    nist = ii[occ == 'NIST']
    n_fixed = 0
    if len(nist):
        emp_nist = upper_text(df.loc[nist, 'contributor_employer'])
        is_school = nist[emp_nist.str.contains(_SCHOOL_EMP_RE, na=False)]
        not_school = nist.difference(is_school)
        if len(is_school):
            df.loc[is_school, 'contributor_occupation'] = 'TEACHER'
            df.loc[is_school, 'occupation_category'] = 'EDUCATION'
            df.loc[is_school, 'occupation_status'] = 'DISCLOSED'
            n_fixed += len(is_school)
        if len(not_school):
            set_missing_occupation(df, not_school)
            n_fixed += len(not_school)
    return n_fixed


# unify SELF-EMPLOYED spellings; run after restore_display_suffixes
def clean_self_employed_variants(df: pd.DataFrame) -> int:
    indiv_idx = individual_index(df)
    n_fixed = 0
    for col in ('contributor_employer', 'contributor_occupation'):
        values = upper_text(df.loc[indiv_idx, col])
        hit = indiv_idx[(values != 'SELF-EMPLOYED') & values.str.match(_SELF_EMP_RE, na=False)]
        if len(hit):
            df.loc[hit, col] = 'SELF-EMPLOYED'
            if col == 'contributor_occupation':
                df.loc[hit, 'occupation_category'] = 'SELF-EMPLOYED'
            n_fixed += len(hit)
    return n_fixed


# null placeholder employer words normalization created late
def clean_junk_status_word_employer(df: pd.DataFrame) -> int:
    indiv_idx = individual_index(df)
    emp = upper_text(df.loc[indiv_idx, 'contributor_employer'])
    collapsed = emp.str.replace(r'\s+', ' ', regex=True)
    hit = indiv_idx[(emp != '') & collapsed.isin(FINAL_NULL_EMPLOYERS)]
    if len(hit):
        df.loc[hit, 'contributor_employer'] = np.nan
        return len(hit)
    return 0
