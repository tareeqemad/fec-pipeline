"""Employer-field junk clearing safety nets."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.occupations.normalize import (
    categorize_occupation,
    categorize_final,
    swap_employer_and_occupation,
)
from fec.config.constants import (
    ADMIN_NOTE_EMPLOYER_RE,
    OK_SHORT_EMPLOYERS,
    REFUSAL_EMPLOYERS,
    RETIRED_TYPO_EMPLOYERS,
)
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
)

# full match, so "RETIREMENT" is untouched
_RETIRED_TOKEN_RE = re.compile(r'^RETIR(?:ED?|EE|D)?$')

# 'RETIRED <tail>': employer or profession tail
_RETIRED_WITH_TAIL_RE = re.compile(r'^RETIRED\b[\s,-]+\S')
# strip leading marker from "RETIRED - ACME"
_RETIRED_PREFIX_RE = re.compile(r'^RETIRED\b[\s,-]+')

_DIGITS_ONLY_RE = re.compile(r'^\d+$')
# one character repeated 4+ times: "XXXXX"
_REPEATED_CHAR_RE = re.compile(r'^(.)\1{3,}$')


# null contributor_employer under mask, optionally set occupation_status
def _null_employer_where(df: pd.DataFrame, mask: pd.Series, *, set_status='EMPLOYER_MISSING') -> int:
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = np.nan
        if set_status is not None:
            df.loc[mask, 'occupation_status'] = set_status
    return n_changed


# clear refusal-word employers like PRIVATE
def clear_refusal_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    mask = is_indiv & df['contributor_employer'].fillna('').str.upper().str.strip().isin(REFUSAL_EMPLOYERS)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    has_real = mask & (df['occupation_status'] == 'DISCLOSED')
    has_not_disclosed = mask & (df['occupation_status'] == 'NOT_DISCLOSED')
    df.loc[has_real, 'contributor_employer'] = pd.NA
    df.loc[has_real, 'occupation_status'] = 'EMPLOYER_MISSING'
    df.loc[has_not_disclosed, 'contributor_employer'] = pd.NA
    return n_fixed


# clear employers that are FEC admin notes
def clear_admin_note_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('').str.upper().str.strip()
    mask = is_indiv & emp.str.match(ADMIN_NOTE_EMPLOYER_RE, na=False)
    return _null_employer_where(df, mask)


# null out truncated 2-char employer junk
def null_short_employer_junk(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer']
    mask = is_indiv & emp.notna() & emp.str.len().le(2) & ~emp.isin(OK_SHORT_EMPLOYERS)
    return _null_employer_where(df, mask, set_status=None)


# clear numeric-only employer like ZIP, phone
def fix_numeric_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.match(_DIGITS_ONLY_RE, na=False)
    return _null_employer_where(df, mask)


# clear employer holding an email address
def fix_email_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.contains('@', na=False, regex=False)
    return _null_employer_where(df, mask)


# clear all-same-character junk employer values
def fix_junk_employer_patterns(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    all_same = is_indiv & emp.str.match(_REPEATED_CHAR_RE, na=False)
    n_fixed = int(all_same.sum())
    if n_fixed:
        df.loc[all_same, 'contributor_employer'] = np.nan
        df.loc[all_same, 'occupation_status'] = 'NOT_DISCLOSED'
    return n_fixed


# normalize RETIRED typos; split off trailing text
def fix_retired_typos(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    emp = df['contributor_employer'].fillna('')
    retired_marker = (
        emp.isin(RETIRED_TYPO_EMPLOYERS)
        | emp.str.fullmatch(_RETIRED_TOKEN_RE, na=False)
    )
    mask = is_indiv & retired_marker & emp.ne('RETIRED')
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_employer'] = 'RETIRED'
        df.loc[mask, 'contributor_occupation'] = 'RETIRED'
        df.loc[mask, 'occupation_category'] = 'RETIRED'
        df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'

    embedded = is_indiv & emp.str.match(_RETIRED_WITH_TAIL_RE, na=False)
    for idx in df.index[embedded]:
        rest = _RETIRED_PREFIX_RE.sub('', emp.at[idx]).strip()
        df.at[idx, 'contributor_employer'] = 'RETIRED'
        occ = str(df.at[idx, 'contributor_occupation'] or '')
        if rest in OCCUPATION_AS_EMPLOYER:
            if not occ or occ == 'RETIRED':
                df.at[idx, 'contributor_occupation'] = rest
        elif len(rest) >= 4 and rest != 'MILITARY':
            if 'previous_employer' not in df.columns:
                df['previous_employer'] = pd.Series(dtype='object', index=df.index)
            if not str(df.at[idx, 'previous_employer'] or '').strip():
                df.at[idx, 'previous_employer'] = rest
        n_fixed += 1
    return n_fixed


# EXECUTIVE is a title, not an industry
_TITLE_SECTOR_WORDS = frozenset({'EXECUTIVE'}) & SECTOR_AS_EMPLOYER

# legal-entity words only, not industry words
_ENTITY_WORD_RE = re.compile(
    r'\b(?:L\.?L\.?C|L\.?L\.?P|L\.?P|INC|CORP|CORPORATION|COMPANY|LTD|PLC'
    r'|GROUP|HOLDINGS|PARTNERS|ENTERPRISES|INDUSTRIES)\b\.?'
)
# name parts that are industries or jobs
_NOT_A_COMPANY_NAME = SECTOR_AS_EMPLOYER | NOT_REAL_EMPLOYER | JOB_TITLE_AS_EMPLOYER


# names a company: entity word, non-industry name
def _occupation_names_a_company(occ: pd.Series, candidates: pd.Series) -> pd.Series:
    upper = occ.fillna('').astype(str).str.strip().str.upper()
    entity = candidates & upper.str.contains(_ENTITY_WORD_RE, na=False)
    result = pd.Series(False, index=occ.index)
    if not entity.any():
        return result
    name = (upper[entity]
            .str.replace(_ENTITY_WORD_RE, ' ', regex=True)
            .str.replace(r'[^\w&]+', ' ', regex=True)
            .str.split().str.join(' '))
    named = (
        name.ne('')
        & ~name.isin(_NOT_A_COMPANY_NAME)
        & ~upper[entity].isin(_NOT_A_COMPANY_NAME)
        & categorize_final(name).eq('OTHER')
    )
    result.loc[named.index] = named.to_numpy()
    return result


# null sector-word employer; EXECUTIVE may swap back
def null_sector_as_employer(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & emp.str.upper().isin(SECTOR_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    title = mask & emp.str.upper().isin(_TITLE_SECTOR_WORDS)
    swap = _occupation_names_a_company(occ, title & occ.ne(''))
    if swap.any():
        swap_employer_and_occupation(df, swap, status='DISCLOSED')
    to_occupation = title & occ.eq('')
    if to_occupation.any():
        df.loc[to_occupation, 'contributor_occupation'] = emp[to_occupation].str.upper()
        df.loc[to_occupation, 'occupation_category'] = categorize_occupation(emp[to_occupation].str.upper())
        df.loc[to_occupation, 'occupation_status'] = 'EMPLOYER_MISSING'

    df.loc[mask & ~swap, 'contributor_employer'] = np.nan
    return n_fixed


# merge 38-char truncated employers into full names
def fix_truncated_employer_38(df: pd.DataFrame) -> int:
    emp_col = df['contributor_employer']
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    indiv_emp = emp_col[is_indiv].dropna()

    trunc_candidates = set(indiv_emp[indiv_emp.str.len() == 38].unique())
    if not trunc_candidates:
        return 0

    all_emps = set(indiv_emp.unique())
    long_emps = {employer for employer in all_emps if isinstance(employer, str) and len(employer) > 38}

    trunc_to_full = {}
    counts = indiv_emp.value_counts()
    for trunc in trunc_candidates:
        matches = [full for full in long_emps if full.startswith(trunc)]
        if len(matches) == 1:
            trunc_to_full[trunc] = matches[0]
        elif len(matches) > 1:
            best = max(matches, key=lambda name: counts.get(name, 0))
            trunc_to_full[trunc] = best

    if not trunc_to_full:
        return 0

    mask = is_indiv & emp_col.isin(trunc_to_full.keys())
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_employer'] = emp_col[mask].map(trunc_to_full)
        if 'contributor_employer_original' in df.columns:
            orig_null = mask & df['contributor_employer_original'].isna()
            df.loc[orig_null, 'contributor_employer_original'] = emp_col[orig_null]
    return n_fixed


# remove web-form --CHOOSE-- prefix from employers
def fix_choose_prefix(df: pd.DataFrame) -> int:
    employer = df['contributor_employer'].fillna('')
    mask = employer.str.startswith('--CHOOSE--')
    changed = int(mask.sum())
    if changed:
        cleaned = employer[mask].str.removeprefix('--CHOOSE--').str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return changed
