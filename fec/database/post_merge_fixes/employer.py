"""Employer-field fixes: same-donor spelling unification, the refusal sweep and the fill steps."""
import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import (
    SKIP_EMPLOYERS, RAW_JUNK_EMPLOYERS, RAW_STATUS_MAP,
    JUNK_EMPLOYER_RE, REFUSAL_EMPLOYERS, SECTOR_AS_EMPLOYER, ADMIN_NOTE_EMPLOYER_RE,
    ROLE_AS_EMPLOYER, OCCUPATION_AS_EMPLOYER,
)
from fec.env import RAW_CSV
from fec.log import get_logger

logger = get_logger(__name__)

_SKIP = SKIP_EMPLOYERS

_WS_RE = re.compile(r'\s+')
# the config patterns are plain strings; compile once for the .str calls below
_JUNK_RE = re.compile(JUNK_EMPLOYER_RE)
_ADMIN_NOTE_RE = re.compile(ADMIN_NOTE_EMPLOYER_RE)


def _employer_typos(df: pd.DataFrame) -> int:
    """AB. Unify near-identical employer spellings within the same donor - the dominant form wins."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in _SKIP]
        if len(real) < 2:
            continue

        counts = grp['contributor_employer'].value_counts()
        canonical = max(real, key=lambda e: counts.get(e, 0))
        cc = counts.get(canonical, 0)

        for other in real:
            if other == canonical:
                continue
            oc = counts.get(other, 0)
            if oc >= cc:
                continue
            lev_ok = (levenshtein(other.upper(), canonical.upper()) <= 2
                      and cc / max(oc, 1) >= 3)
            fuzzy_ok = SequenceMatcher(
                None, other.upper(), canonical.upper()).ratio() * 100 >= 90
            if lev_ok or fuzzy_ok:
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == other)
                df.loc[mask, 'contributor_employer'] = canonical
                n_fixed += int(mask.sum())
    return n_fixed


def _employer_substring_variants(df: pd.DataFrame) -> int:
    """AG. Same-donor employer substring variants - the >=3x more frequent spelling wins."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in _SKIP]
        if len(real) < 2:
            continue

        counts = grp['contributor_employer'].value_counts()
        fixed_in_group = set()

        for i, a in enumerate(real):
            for b in real[i + 1:]:
                au, bu = a.upper(), b.upper()
                if au not in bu and bu not in au:
                    continue

                ca, cb = counts.get(a, 0), counts.get(b, 0)

                if ca >= 3 * cb and cb > 0:
                    winner, loser = a, b
                elif cb >= 3 * ca and ca > 0:
                    winner, loser = b, a
                else:
                    continue

                if loser in fixed_in_group:
                    continue

                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == loser)
                n = int(mask.sum())
                if n:
                    df.loc[mask, 'contributor_employer'] = winner
                    fixed_in_group.add(loser)
                    n_fixed += n

    return n_fixed


# Refusal/placeholder words to null out of the employer field - unlike RETIRED /
# SELF-EMPLOYED / NOT EMPLOYED (kept as status values) these carry no information.
# Internal whitespace is collapsed so "N A" and "N/A" both match.
_NULL_EMPLOYER_WORDS = (
    REFUSAL_EMPLOYERS
    | {'N/A', 'NA', 'N A', 'NONE', 'NOT APPLICABLE', 'NOT APPLICAABLE',
       'NOT DISCLOSED', 'INFORMATION REQUESTED',
       'INFORMATION REQUESTED PER BEST EFFORTS', 'PHYSICAN', 'SELP EMPLOYED'}
)


def _null_refusal_employers(df: pd.DataFrame) -> int:
    """AS. Null refusal/placeholder employers that AK/AL re-filled from raw; valid status words stay."""
    emp = df['contributor_employer'].fillna('').astype(str)
    collapsed = emp.str.strip().str.upper().str.replace(_WS_RE, ' ', regex=True)
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & collapsed.isin(_NULL_EMPLOYER_WORDS)
    )
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


def _fill_employer_from_donor(df: pd.DataFrame) -> int:
    """AI. Fill NaN employer from same donor's other records (needs donor_key)."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    null_emp = indiv[indiv['contributor_employer'].isna()]
    if null_emp.empty:
        return 0

    n_fixed = 0
    for dk, grp in null_emp.groupby('donor_key'):
        all_recs = df[(df['donor_key'] == dk) & (df['entity_type'] == 'INDIVIDUAL')]
        real_recs = all_recs[all_recs['contributor_employer'].notna()
                             & ~all_recs['contributor_employer'].isin(_SKIP)]
        if real_recs.empty:
            continue

        # latest-dated qualifying filing - closest in time = best estimate
        main_emp = real_recs.sort_values(
            'contribution_receipt_date', na_position='first'
        )['contributor_employer'].iloc[-1]
        emp_recs = all_recs[all_recs['contributor_employer'] == main_emp]
        main_occ = emp_recs['contributor_occupation'].dropna().value_counts()
        main_occ = main_occ.index[0] if len(main_occ) > 0 else None
        main_cat = emp_recs['occupation_category'].dropna().value_counts()
        main_cat = main_cat.index[0] if len(main_cat) > 0 else None

        mask = (df['donor_key'] == dk) & df['contributor_employer'].isna()
        df.loc[mask, 'contributor_employer'] = main_emp
        df.loc[mask, 'occupation_status'] = 'DERIVED'   # employer filled from donor history
        if main_occ and df.loc[mask, 'contributor_occupation'].isna().all():
            df.loc[mask, 'contributor_occupation'] = main_occ
        if main_cat:
            df.loc[mask, 'occupation_category'] = main_cat
        n_fixed += int(mask.sum())

    return n_fixed


# status-word occupation/category -> the employer value it implies (step AK)
_EMP_FROM_OCCUPATION = {
    'RETIRED': 'RETIRED',
    'HOMEMAKER': 'HOMEMAKER',
    'HOUSEWIFE': 'HOMEMAKER',
    'NOT EMPLOYED': 'NOT EMPLOYED',
    'STUDENT': 'STUDENT',
    'UNEMPLOYED': 'NOT EMPLOYED',
    'SELF-EMPLOYED': 'SELF-EMPLOYED',
}
_EMP_FROM_CATEGORY = {
    'RETIRED': 'RETIRED',
    'NOT EMPLOYED': 'NOT EMPLOYED',
    'HOMEMAKER': 'HOMEMAKER',
    'STUDENT': 'STUDENT',
    'SELF-EMPLOYED': 'SELF-EMPLOYED',
}


def _fill_employer_from_occupation(df: pd.DataFrame) -> int:
    """AK. Empty employer + status-word occupation/category -> employer = that status; else recover from raw."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    empty_emp = df['contributor_employer'].isna() | (df['contributor_employer'] == '')
    occ = df['contributor_occupation'].fillna('')

    n = 0
    for occ_val, emp_val in _EMP_FROM_OCCUPATION.items():
        mask = is_indiv & empty_emp & (occ == occ_val)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Set from occupation_category
    still_empty = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    for cat, emp_val in _EMP_FROM_CATEGORY.items():
        mask = still_empty & (df['occupation_category'] == cat)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Try to recover from raw FEC data
    still_empty2 = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    if still_empty2.any():
        n += _fill_employer_from_raw(df, still_empty2)

    return n


def _fill_employer_from_raw(df: pd.DataFrame, empty_mask: pd.Series) -> int:
    """AL. Recover employer from the same person's raw FEC filings (name+state key)."""
    if not RAW_CSV.exists():
        return 0

    raw = pd.read_csv(RAW_CSV, dtype=str, usecols=['contributor_name', 'contributor_state', 'contributor_employer'], low_memory=False)

    empty_rows = df[empty_mask]
    empty_keys = {
        str(r['contributor_name']).strip().upper() + '|' + str(r['contributor_state']).strip().upper()
        for _, r in empty_rows.iterrows()
    }

    raw['_key'] = raw['contributor_name'].fillna('').str.strip().str.upper() + '|' + raw['contributor_state'].fillna('').str.strip().str.upper()
    raw_matches = raw[raw['_key'].isin(empty_keys)]

    key_to_emp = {}
    for key in empty_keys:
        person = raw_matches[raw_matches['_key'] == key]
        emps = person['contributor_employer'].fillna('').str.strip()

        # sector/role/title/refusal words are blanked or converted upstream on
        # purpose - never re-recover them from raw
        real = emps[~emps.str.upper().isin(RAW_JUNK_EMPLOYERS)
                    & ~emps.str.upper().isin(RAW_STATUS_MAP.keys())
                    & ~emps.str.upper().isin(SECTOR_AS_EMPLOYER)
                    & ~emps.str.upper().isin(ROLE_AS_EMPLOYER)
                    & ~emps.str.upper().isin(OCCUPATION_AS_EMPLOYER)
                    & ~emps.str.upper().isin(REFUSAL_EMPLOYERS)
                    & (emps.str.len() > 2)]
        # same structural-junk patterns the cleaner uses (emails, dates, masked
        # digits, admin notes) so junk blanked upstream is not re-recovered
        real = real[~real.str.contains('@', na=False)
                    & ~real.str.upper().str.match(_JUNK_RE, na=False)
                    & ~real.str.upper().str.match(_ADMIN_NOTE_RE, na=False)]
        if len(real) > 0:
            key_to_emp[key] = real.value_counts().index[0]
            continue

        # Try status word
        status = emps[emps.str.upper().isin(RAW_STATUS_MAP.keys())]
        if len(status) > 0:
            raw_val = status.value_counts().index[0].upper()
            key_to_emp[key] = RAW_STATUS_MAP.get(raw_val, raw_val)

    n = 0
    for idx in df[empty_mask].index:
        name = str(df.at[idx, 'contributor_name']).strip().upper()
        state = str(df.at[idx, 'contributor_state']).strip().upper()
        key = f'{name}|{state}'
        if key in key_to_emp:
            df.at[idx, 'contributor_employer'] = key_to_emp[key]
            df.at[idx, 'occupation_status'] = 'DERIVED'   # recovered from donor's raw filings
            n += 1

    if n:
        logger.info(f"    AL. Recovered {n} employers from raw FEC filings")
    return n
