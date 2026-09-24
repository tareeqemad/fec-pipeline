"""Employer-field junk clearing; swap and self-employment fixes live in employer_swaps."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.occupations import _categorize, _categorize_final
from fec.config.constants import (
    REFUSAL_EMPLOYERS, OK_SHORT_EMPLOYERS, SECTOR_AS_EMPLOYER, ADMIN_NOTE_EMPLOYER_RE,
    OCCUPATION_AS_EMPLOYER, RETIRED_TYPO_EMPLOYERS, NOT_REAL_EMPLOYER,
    JOB_TITLE_AS_EMPLOYER,
)

from .employer_swaps import _swap_occ_emp_fields

# Safe structural variants of the status word: RETIR / RETIRE / RETIRED /
# RETIREE / RETIRD. Full-match only, so "RETIREMENT" is never touched.
_RETIRED_TOKEN_RE = re.compile(r'^RETIR(?:ED?|EE|D)?$')

# 'RETIRED <tail>': the tail is a previous employer or a profession word
_RETIRED_WITH_TAIL_RE = re.compile(r'^RETIRED\b[\s,-]+\S')
_RETIRED_PREFIX_RE = re.compile(r'^RETIRED\b[\s,-]+')

_DIGITS_ONLY_RE = re.compile(r'^\d+$')
_REPEATED_CHAR_RE = re.compile(r'^(.)\1{3,}$')


def _null_employer_where(df: pd.DataFrame, mask: pd.Series, *, set_status='EMPLOYER_MISSING') -> int:
    """Null contributor_employer under mask, clear employer_name_normalized, optionally set occupation_status; returns rows changed."""
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = np.nan
        if set_status is not None:
            df.loc[mask, 'occupation_status'] = set_status
        if 'employer_name_normalized' in df.columns:
            df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n_changed


def _clear_refusal_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """M. Employer is a refusal word (PRIVATE, CONFIDENTIAL): clear it."""
    mask = is_indiv & df['contributor_employer'].fillna('').str.upper().str.strip().isin(REFUSAL_EMPLOYERS)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    has_real = mask & (df['occupation_status'] == 'DISCLOSED')
    has_not_disclosed = mask & (df['occupation_status'] == 'NOT_DISCLOSED')
    df.loc[has_real, 'contributor_employer'] = pd.NA
    df.loc[has_real, 'employer_name_normalized'] = pd.NA
    df.loc[has_real, 'occupation_status'] = 'EMPLOYER_MISSING'
    df.loc[has_not_disclosed, 'contributor_employer'] = pd.NA
    df.loc[has_not_disclosed, 'employer_name_normalized'] = pd.NA
    return n_fixed


def _clear_admin_note_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """FEC admin-note phrasing in employer (PER BEST EFFORTS, REQUEST SENT...); full-anchored so a real name CONTAINING such a word is kept."""
    emp = df['contributor_employer'].fillna('').str.upper().str.strip()
    mask = is_indiv & emp.str.match(ADMIN_NOTE_EMPLOYER_RE, na=False)
    return _null_employer_where(df, mask)


def _clear_orphan_normalized(df: pd.DataFrame) -> int:
    """N. Clear employer_name_normalized where contributor_employer is NULL."""
    mask = df['contributor_employer'].isna() & df['employer_name_normalized'].notna()
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'employer_name_normalized'] = pd.NA
    return n_fixed


def _null_short_employer_junk(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """O. Null out truncated 2-char employer junk."""
    emp = df['contributor_employer']
    mask = is_indiv & emp.notna() & emp.str.len().le(2) & ~emp.isin(OK_SHORT_EMPLOYERS)
    return _null_employer_where(df, mask, set_status=None)


def _fix_numeric_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """Q. Final pass: numeric-only employer (card number, ZIP, phone) -> NaN."""
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.match(_DIGITS_ONLY_RE, na=False)
    return _null_employer_where(df, mask)


def _fix_email_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """R. Final pass: email in employer -> NaN."""
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.contains('@', na=False, regex=False)
    return _null_employer_where(df, mask)


def _fix_junk_employer_patterns(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """S. All-same-char junk employers (XXXXXXXXX, AAAAAAA) -> NaN."""
    emp = df['contributor_employer'].fillna('')
    all_same = is_indiv & emp.str.match(_REPEATED_CHAR_RE, na=False)
    n_fixed = int(all_same.sum())
    if n_fixed:
        df.loc[all_same, 'contributor_employer'] = np.nan
        df.loc[all_same, 'occupation_status'] = 'NOT_DISCLOSED'
    return n_fixed


def _fix_retired_typos(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """T. RETIRED typos normalized; 'RETIRED <X>' splits X to previous_employer (company) or occupation (profession word)."""
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


# EXECUTIVE is a title, not an industry. It stays in SECTOR_AS_EMPLOYER (the
# previous-employer contract and the raw-employer recovery must keep reading it
# as "no company"), but here an occupation box holding a named company
# (NORTH INDUSTRIES) is swapped back instead of the title being lost. Beside an
# industry it reads "<industry> executive" (EXECUTIVE / COSMETICS, EXECUTIVE /
# HEALTHCARE): the employer is nulled and the words are kept, as for any sector.
_TITLE_SECTOR_WORDS = frozenset({'EXECUTIVE'}) & SECTOR_AS_EMPLOYER

# Legal-entity words only. The broad company marker of the AD swap
# (_COMPANY_NAME_RE) also matches industry words (HEALTHCARE, INSURANCE, MEDIA,
# FINANCIAL, SERVICES, MANAGEMENT, GLOBAL, HOSPITAL), any "X & Y" (OIL & GAS)
# and CO-FOUNDER, which beside EXECUTIVE are lines of work, not employers.
_ENTITY_WORD_RE = re.compile(
    r'\b(?:L\.?L\.?C|L\.?L\.?P|L\.?P|INC|CORP|CORPORATION|COMPANY|LTD|PLC'
    r'|GROUP|HOLDINGS|PARTNERS|ENTERPRISES|INDUSTRIES)\b\.?'
)
# a name part that is itself an industry, a kind of workplace or a job
_NOT_A_COMPANY_NAME = SECTOR_AS_EMPLOYER | NOT_REAL_EMPLOYER | JOB_TITLE_AS_EMPLOYER


def _occupation_names_a_company(occ: pd.Series, candidates: pd.Series) -> pd.Series:
    """EXECUTIVE / <text>: the text is the company only when it carries a legal-entity word AND the name beside that word is no line of work.

    NORTH INDUSTRIES -> yes. HEALTHCARE COMPANY, INSURANCE GROUP, INVESTMENT
    GROUP, a bare LLC -> no: the name part is empty, a sector / non-company
    word, or text a category rule recognises as a line of work.
    """
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
        & _categorize_final(name).eq('OTHER')
    )
    result.loc[named.index] = named.to_numpy()
    return result


def _null_sector_as_employer(df: pd.DataFrame) -> int:
    """AD2. Industry/sector word in employer -> NULL (not a company; occupation kept). The title word EXECUTIVE is swapped back when the occupation box names a company, and moved into an empty occupation box."""
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
        _swap_occ_emp_fields(df, swap, status='DISCLOSED')
    to_occupation = title & occ.eq('')
    if to_occupation.any():
        df.loc[to_occupation, 'contributor_occupation'] = emp[to_occupation].str.upper()
        df.loc[to_occupation, 'occupation_category'] = _categorize(emp[to_occupation].str.upper())
        df.loc[to_occupation, 'occupation_status'] = 'EMPLOYER_MISSING'

    df.loc[mask & ~swap, 'contributor_employer'] = np.nan
    return n_fixed


def _fix_truncated_employer_38(df: pd.DataFrame) -> int:
    """AI. FEC truncates employer at 38 chars; merge truncated names with their unique (or most common) longer version."""
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


def _fix_choose_prefix(df: pd.DataFrame) -> int:
    """Remove the web-form --CHOOSE-- prefix from employer names."""
    employer = df['contributor_employer'].fillna('')
    mask = employer.str.startswith('--CHOOSE--')
    changed = int(mask.sum())
    if changed:
        cleaned = employer[mask].str.removeprefix('--CHOOSE--').str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return changed
