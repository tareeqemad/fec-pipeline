"""Clean junk occupation and employer values."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx, _set_missing
from fec.cleaning.occupations import _categorize
from fec.config.constants import JUNK_EMPLOYER_RE
from fec.config.occupation_rules.rules import (
    FINAL_JUNK_EMPLOYERS,
    FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS,
    OCCUPATION_KEYWORDS,
)

# matched against _norm() output (already uppercase), so case-sensitive on purpose
_SCHOOL_EMP_RE = re.compile(
    r'SCHOOL|ACADEMY|COLLEGE|UNIVERSITY|EDUCATION|MDCPS|ISD\b|UNIFIED|DISTRICT',
)

# anchored: SELF / SELF EMPLOYED / SELFEMPLOYED / SELF-EMP etc. match; SELFRIDGES and trailing text do not
_SELF_EMP_RE = re.compile(r'^SELF(?:[\s\-/]*EMP(?:LOY\w*)?)?$')

# SELF with a tail ("SELF - ACME"). A named company is preserved; generic
# descriptions such as "SELF EMPLOYED LAW OFFICE" remain SELF-EMPLOYED.
_SELF_PREFIX_RE = re.compile(r'^SELF[\s,/\-]+', re.IGNORECASE)
# leading "SELF" (and optional "EMPLOYED") prefix to strip: "SELF - ACME LLC"
_SELF_PREFIX_STRIP_RE = re.compile(r'^SELF[\s,/\-]+(?:EMPLOYED[\s,/\-]*)?')
# a company-type word marking the tail as a real firm name, not a description
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

# an occupation ending in a bare number: "TEACHER 12345"
_OCC_NUM_TAIL_RE = re.compile(r'^[A-Z ]+\s+\d+$')
# the trailing number itself, to strip it: " 12345" at end of string
_TRAILING_NUM_RE = re.compile(r'\s+\d+$')

# run all final junk-cleaning passes on employer/occupation
def clean_remaining_junk(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Final pass for junk that slipped through; returns (df, n_fixed)."""
    indiv_idx = _indiv_idx(df)
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
    emp = _norm(df.loc[ii, 'contributor_employer'])
    xxx = ii[emp.isin({'XXX', 'XX', 'XXXX', 'X'})]
    if len(xxx):
        df.loc[xxx, 'contributor_employer'] = np.nan
        return len(xxx)
    return 0


# blank employers matching structurally-junk patterns
def _clean_junk_employer_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    """Blank employers that are structurally not a company name (dates, numbers, masked digits)."""
    emp = _norm(df.loc[ii, 'contributor_employer'])
    junk = ii[(emp != '') & emp.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        df.loc[junk, 'contributor_employer'] = np.nan
        return len(junk)
    return 0


# blank occupations too short to be real
def _clean_junk_short_occ(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    short = ii[(occ.str.len() <= 2) & (occ != '') & ~occ.isin(FINAL_SHORT_OCCUPATIONS)]
    if len(short):
        _set_missing(df, short)
        return len(short)
    return 0


# blank structurally junk occupations, mark missing for refill
def _clean_junk_occ_patterns(df: pd.DataFrame, ii: pd.Index) -> int:
    """Blank structurally junk occupations; set MISSING so a later same-donor pass can refill."""
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    junk = ii[(occ != '') & occ.str.match(JUNK_EMPLOYER_RE, na=False)]
    if len(junk):
        _set_missing(df, junk)
        return len(junk)
    return 0


# strip a trailing number from an occupation
def _clean_junk_number_tail(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    num_tail = ii[occ.str.contains(_OCC_NUM_TAIL_RE, na=False)]
    if len(num_tail):
        df.loc[num_tail, 'contributor_occupation'] = occ[num_tail].str.replace(_TRAILING_NUM_RE, '', regex=True)
        return len(num_tail)
    return 0


# bare LLP occupation becomes attorney, legal category
def _clean_junk_llp_standalone(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    llp = ii[occ == 'LLP']
    if len(llp):
        df.loc[llp, 'contributor_occupation'] = 'ATTORNEY'
        df.loc[llp, 'occupation_category'] = 'LEGAL'
        df.loc[llp, 'occupation_status'] = 'DISCLOSED'
        return len(llp)
    return 0


# mark occupation missing when text says not disclosed
def _clean_junk_not_disclosed(df: pd.DataFrame, ii: pd.Index) -> int:
    nd_text = ii[
        (_norm(df.loc[ii, 'contributor_occupation']) == 'NOT DISCLOSED')
        & (df.loc[ii, 'occupation_status'] != 'NOT_DISCLOSED')
    ]
    if len(nd_text):
        _set_missing(df, nd_text)
        return len(nd_text)
    return 0


# fix several known employer/occupation swap and junk patterns
def _clean_junk_employer_fixes(df: pd.DataFrame, ii: pd.Index) -> int:
    n_fixed = 0
    emp = _norm(df.loc[ii, 'contributor_employer'])
    occ = _norm(df.loc[ii, 'contributor_occupation'])

    # employer = DOCTOR/PHYSICIAN when occ is medical: SELF-EMPLOYED
    doc_emp = ii[emp.isin({'DOCTOR', 'PHYSICIAN'}) & occ.isin({'PHYSICIAN', 'DOCTOR', 'MEDICAL DOCTOR'})]
    if len(doc_emp):
        df.loc[doc_emp, 'contributor_employer'] = 'SELF-EMPLOYED'
        n_fixed += len(doc_emp)

    # employer = occupation word + occ = SELF-EMPLOYED: swap
    swap_se = ii[emp.isin(OCCUPATION_KEYWORDS) & (occ == 'SELF-EMPLOYED')]
    if len(swap_se):
        real_occ = df.loc[swap_se, 'contributor_employer'].copy()
        df.loc[swap_se, 'contributor_employer'] = 'SELF-EMPLOYED'
        df.loc[swap_se, 'contributor_occupation'] = real_occ
        df.loc[swap_se, 'occupation_category'] = _categorize(df.loc[swap_se, 'contributor_occupation'])
        df.loc[swap_se, 'occupation_status'] = 'DISCLOSED'
        n_fixed += len(swap_se)

    # employer junk words
    emp_junk = ii[emp.isin(FINAL_JUNK_EMPLOYERS)]
    if len(emp_junk):
        df.loc[emp_junk, 'contributor_employer'] = np.nan
        n_fixed += len(emp_junk)

    # SELF variants with a tail
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


# resolve NIST occupation: teacher if school employer, else missing
def _clean_junk_nist(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    nist = ii[occ == 'NIST']
    n_fixed = 0
    if len(nist):
        emp_nist = _norm(df.loc[nist, 'contributor_employer'])
        is_school = nist[emp_nist.str.contains(_SCHOOL_EMP_RE, na=False)]
        not_school = nist.difference(is_school)
        if len(is_school):
            df.loc[is_school, 'contributor_occupation'] = 'TEACHER'
            df.loc[is_school, 'occupation_category'] = 'EDUCATION'
            df.loc[is_school, 'occupation_status'] = 'DISCLOSED'
            n_fixed += len(is_school)
        if len(not_school):
            _set_missing(df, not_school)
            n_fixed += len(not_school)
    return n_fixed


# unify self-employed spelling variants to one form
def _clean_self_employed_variants(df: pd.DataFrame) -> int:
    """Unify self-employed spellings to SELF-EMPLOYED; must run after restore_display_suffixes."""
    indiv_idx = _indiv_idx(df)
    n_fixed = 0
    for col in ('contributor_employer', 'contributor_occupation'):
        values = _norm(df.loc[indiv_idx, col])
        hit = indiv_idx[(values != 'SELF-EMPLOYED') & values.str.match(_SELF_EMP_RE, na=False)]
        if len(hit):
            df.loc[hit, col] = 'SELF-EMPLOYED'
            if col == 'contributor_occupation':
                df.loc[hit, 'occupation_category'] = 'SELF-EMPLOYED'
            n_fixed += len(hit)
    return n_fixed


# null placeholder employer words normalization created late
def _clean_junk_status_word_employer(df: pd.DataFrame) -> int:
    """Final sweep: null refusal/placeholder employers that normalization created late ("N A" -> "N/A")."""
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    collapsed = emp.str.replace(r'\s+', ' ', regex=True)
    hit = indiv_idx[(emp != '') & collapsed.isin(FINAL_NULL_EMPLOYERS)]
    if len(hit):
        df.loc[hit, 'contributor_employer'] = np.nan
        return len(hit)
    return 0
