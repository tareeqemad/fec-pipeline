"""Employer-field junk clearing; swap and self-employment fixes live in employer_swaps."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.work.normalize import (
    _categorize,
    categorize_final,
    swap_employer_and_occupation,
)
from fec.config.constants import (
    REFUSAL_EMPLOYERS,
    RETIRED_TYPO_EMPLOYERS,
)
from fec.config.not_employers import (
    JOB_TITLE_AS_EMPLOYER,
    NOT_REAL_EMPLOYER,
    OCCUPATION_AS_EMPLOYER,
    SECTOR_AS_EMPLOYER,
)

# Variants of RETIRED, full match only, so RETIREMENT stays.
_RETIRED_TOKEN_RE = re.compile(r'^RETIR(?:ED?|EE|D)?$')

# 'RETIRED <tail>': a previous employer or profession word.
_RETIRED_WITH_TAIL_RE = re.compile(r'^RETIRED\b[\s,-]+\S')
# Leading RETIRED marker to strip: "RETIRED - ACME".
_RETIRED_PREFIX_RE = re.compile(r'^RETIRED\b[\s,-]+')



# null contributor_employer under mask, optionally set occupation_status
def _null_employer_where(df: pd.DataFrame, mask: pd.Series, *, set_status='EMPLOYER_MISSING') -> int:
    """Null contributor_employer under mask, optionally set occupation_status; returns rows changed."""
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, 'contributor_employer'] = np.nan
        if set_status is not None:
            df.loc[mask, 'occupation_status'] = set_status
    return n_changed


# clear employer when it is a refusal word (PRIVATE, CONFIDENTIAL)
def _clear_refusal_employers(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """M. Employer is a refusal word (PRIVATE, CONFIDENTIAL): clear it."""
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


# clear employer when it holds an email address
def _fix_email_employer_final(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """R. Final pass: email in employer -> NaN."""
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.str.contains('@', na=False, regex=False)
    return _null_employer_where(df, mask)


# normalize RETIRED typos, splitting any trailing text off
def _fix_retired_typos(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """RETIRED typos fixed; 'RETIRED <X>' moves X out."""
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


# EXECUTIVE is a title when beside a company.
_TITLE_SECTOR_WORDS = frozenset({'EXECUTIVE'}) & SECTOR_AS_EMPLOYER

# Require company words; sector words and '&' are not enough.
_ENTITY_WORD_RE = re.compile(
    r'\b(?:L\.?L\.?C|L\.?L\.?P|L\.?P|INC|CORP|CORPORATION|COMPANY|LTD|PLC'
    r'|GROUP|HOLDINGS|PARTNERS|ENTERPRISES|INDUSTRIES)\b\.?'
)
# A name part that is an industry, workplace or job.
_NOT_A_COMPANY_NAME = SECTOR_AS_EMPLOYER | NOT_REAL_EMPLOYER | JOB_TITLE_AS_EMPLOYER


# Require a company word plus a non-sector, non-job name.
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


# Clear sector names; first recover a company or move titles.
def _null_sector_as_employer(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('').str.upper()
    occ = df['contributor_occupation'].fillna('')
    mask = is_indiv & emp.isin(SECTOR_AS_EMPLOYER)
    n_fixed = int(mask.sum())
    if not n_fixed:
        return 0

    title = mask & emp.isin(_TITLE_SECTOR_WORDS)
    swap = _occupation_names_a_company(occ, title & occ.ne(''))
    if swap.any():
        swap_employer_and_occupation(df, swap, status='DISCLOSED')
    to_occupation = title & occ.eq('')
    if to_occupation.any():
        df.loc[to_occupation, 'contributor_occupation'] = emp[to_occupation]
        df.loc[to_occupation, 'occupation_category'] = _categorize(emp[to_occupation])
        df.loc[to_occupation, 'occupation_status'] = 'EMPLOYER_MISSING'

    df.loc[mask & ~swap, 'contributor_employer'] = np.nan
    return n_fixed


# Truncated names expand only to documented full names.
def _fix_truncated_employer_38(df: pd.DataFrame) -> int:
    from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
    return apply_reviewed_employer_names(df, ("contributor_employer",))


# remove the web-form --CHOOSE-- prefix from employer names
def _fix_choose_prefix(df: pd.DataFrame) -> int:
    """Remove the web-form --CHOOSE-- prefix from employer names."""
    employer = df['contributor_employer'].fillna('')
    mask = employer.str.startswith('--CHOOSE--')
    changed = int(mask.sum())
    if changed:
        cleaned = employer[mask].str.removeprefix('--CHOOSE--').str.strip()
        df.loc[mask, 'contributor_employer'] = cleaned.replace({'': np.nan})
    return changed
