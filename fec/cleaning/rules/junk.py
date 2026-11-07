"""Clean junk occupation and employer values."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _norm, _set_missing
from fec.cleaning.work.employer_aliases import employer_rule_key
from fec.cleaning.work.company import is_company
from fec.cleaning.work.previous import SELF_EMPLOYED_TAIL_RE
from fec.cleaning.work.status import is_job_title
from fec.cleaning.work.employer_aliases import documented_employer_name
from fec.config.constants import JUNK_EMPLOYER_RE, OK_SHORT_EMPLOYERS, REFUSAL_EMPLOYERS
from fec.config.data import MISSING_VALUES
from fec.config.occupation_rules.rules import (
    CATEGORY_OVERRIDES,
    FINAL_JUNK_EMPLOYERS,
    FINAL_NULL_EMPLOYERS,
    FINAL_SHORT_OCCUPATIONS,
)

# SELF with a tail: named companies are kept.
_SELF_PREFIX_RE = re.compile(r'^SELF[\s,/\-]+', re.IGNORECASE)
# Leading SELF prefix to strip: "SELF - ACME LLC".
_SELF_PREFIX_STRIP_RE = re.compile(r'^SELF[\s,/\-]+(?:EMPLOYED[\s,/\-]*)?')
_SELF_COMPANY_OVERRIDES = {
    'SELF EMPLOYED- STANDARDIZED SUCCESS L': 'STANDARDIZED SUCCESS LLC',
    'SELF EMPLOYED AND TOTAL REALTY': 'TOTAL REALTY',
    'SELF AND STEVENSON UNIVERSITY': 'STEVENSON UNIVERSITY',
}
_GENERIC_SELF_TAILS = {'LAW OFFICE', 'COMPANY OWNER', 'PRIVATE CONTRACTOR', 'CONTRACTOR'}

# an occupation ending in a bare number: "TEACHER 12345"
_OCC_NUM_TAIL_RE = re.compile(r'^[A-Z ]+\s+\d+$')
# The trailing number to strip.
_TRAILING_NUM_RE = re.compile(r'\s+\d+$')

_EMPLOYER_PLACEHOLDER_WORDS = FINAL_JUNK_EMPLOYERS | FINAL_NULL_EMPLOYERS | REFUSAL_EMPLOYERS


def placeholder_employer(values: pd.Series, *, words: bool = True) -> pd.Series:
    """True for employer text that names no employer.

    Always: numbers, emails, X runs, symbols or dates, and short values
    that are not a known short name. With words, also placeholder and
    refusal words (VARIOUS, N/A, PRIVATE, INFORMATION REQUESTED, ...).
    """
    text = _norm(values)
    junk = (
        text.str.contains('@', regex=False)
        | text.str.fullmatch(r'X+', na=False)
        | text.str.match(JUNK_EMPLOYER_RE, na=False)
        | (text.str.len().le(2) & ~text.isin(OK_SHORT_EMPLOYERS) & ~text.isin(MISSING_VALUES))
    )
    if words:
        junk |= text.str.replace(r'\s+', ' ', regex=True).isin(_EMPLOYER_PLACEHOLDER_WORDS)
    return text.ne('') & junk


def clear_placeholder_employers(df: pd.DataFrame, *, words: bool = True) -> int:
    """Blank employers that name no employer; a documented email keeps its company."""
    employer = df['contributor_employer']
    junk = placeholder_employer(employer, words=words)
    if not junk.any():
        return 0
    # The same placeholder in the occupation, unless the category table lists it.
    occupation = df['contributor_occupation'] if 'contributor_occupation' in df else None
    if occupation is not None:
        same = junk & occupation.map(employer_rule_key).eq(employer.map(employer_rule_key))
        same &= ~_norm(occupation).isin(CATEGORY_OVERRIDES)
        if same.any():
            _set_missing(df, df.index[same])
    documented = employer[junk].map(documented_employer_name)
    df.loc[junk, 'contributor_employer'] = documented.where(documented.notna(), np.nan)
    return int(junk.sum())


def clear_placeholder_employer_words(df: pd.DataFrame) -> int:
    """Blank structural junk and placeholder words for people."""
    people = df['entity_type'].eq('INDIVIDUAL')
    if not people.any():
        return 0
    work = ['contributor_employer', 'contributor_occupation', 'occupation_category']
    view = df.loc[people, work].copy()
    n = clear_placeholder_employers(view)
    df.loc[people, work] = view
    return n


# run all final junk-cleaning passes on employer/occupation
def clean_remaining_junk(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Final pass for junk that slipped through; returns (df, n_fixed)."""
    indiv_idx = _indiv_idx(df)
    n_fixed = 0
    n_fixed += clear_placeholder_employer_words(df)
    n_fixed += _clean_junk_occupations(df, indiv_idx)
    n_fixed += _clean_junk_number_tail(df, indiv_idx)
    n_fixed += _clean_junk_llp_standalone(df, indiv_idx)
    n_fixed += _clean_junk_employer_fixes(df, indiv_idx)
    return df, n_fixed


def _clean_junk_occupations(df: pd.DataFrame, ii: pd.Index) -> int:
    """Blank short or junk occupations; mark MISSING for refill."""
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    short = (occ.str.len() <= 2) & (occ != '') & ~occ.isin(FINAL_SHORT_OCCUPATIONS)
    pattern = (occ != '') & occ.str.match(JUNK_EMPLOYER_RE, na=False)
    junk = ii[short | pattern]
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


# a bare LLP or NIST names no occupation
def _clean_junk_llp_standalone(df: pd.DataFrame, ii: pd.Index) -> int:
    occ = _norm(df.loc[ii, 'contributor_occupation'])
    junk = ii[occ.isin({'LLP', 'NIST'})]
    if len(junk):
        _set_missing(df, junk)
    return len(junk)


# fix several known employer/occupation swap and junk patterns
def _clean_junk_employer_fixes(df: pd.DataFrame, ii: pd.Index) -> int:
    n_fixed = 0

    # "CONSULTANT (SELF-EMPLOYED)": the marker ends the text; a firm before it stays.
    emp_raw = df.loc[ii, 'contributor_employer'].fillna('')
    tailed = ii[emp_raw.str.upper().str.contains(SELF_EMPLOYED_TAIL_RE, na=False)]
    for idx in tailed:
        upper = emp_raw.at[idx].strip().upper()
        head = SELF_EMPLOYED_TAIL_RE.sub('', upper).strip(' -,/(:')
        df.at[idx, 'contributor_employer'] = head if is_company(pd.Series([head])).iloc[0] else 'SELF-EMPLOYED'
        if head and is_job_title(head) and not str(df.at[idx, 'contributor_occupation'] or '').strip():
            df.at[idx, 'contributor_occupation'] = head
        n_fixed += 1
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
            company = _SELF_COMPANY_OVERRIDES.get(employer_rule_key(original))
            clean_tail = tail.strip('() ').strip()
            if (not company and clean_tail not in _GENERIC_SELF_TAILS
                    and is_company(pd.Series([clean_tail])).iloc[0]):
                company = clean_tail
            df.at[idx, 'contributor_employer'] = company or 'SELF-EMPLOYED'
            n_fixed += 1

    return n_fixed
