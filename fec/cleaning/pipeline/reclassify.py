"""Fix entity types the filer got wrong, and restore the fields the flip cleared."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.occupations import _categorize, _normalize_text
from fec.config import (
    EMPLOYER_NORMALIZE, INDIV_NAME_RE, MISSING_VALUES,
    OCCUPATION_FIXES, OCCUPATION_NORMALIZE, ORG_KEYWORDS,
)
from fec.config.constants import OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS

# Name-token signals for the ORGANIZATION vs COMMITTEE/PAC split: US legal and
# business suffixes plus sector words, trimmed to tokens with a proven effect
# on the real data (per-token A/B trial, 2026-07). LLP looks unused in cleaned
# names only because the tail is stripped later — it decides the law-firm rows.
# STEEL is also a candidate surname ("STEEL FOR CONGRESS"); the committee-signal
# check outranks the business tokens, so those names stay committees.
_ORG_BUSINESS_RE = re.compile(
    r'\b(?:LLC|INC|CORP|CORPORATION|LLP|LP|HOLDINGS?|ENTERPRISES?'
    r'|VENTURES?|PROPERTIES|PROPERTY|REALTY|REAL ESTATE|MANAGEMENT|PARTNERS'
    r'|PARTNERSHIP|CAPITAL|GROUP|ASSOCIATES|COMPANY|INDUSTRIES|INVESTMENTS?'
    r'|BANK|INSURANCE|FOUNDATION|TRUST|MEDIA|DEVELOPMENT|LENDING|ACQUISITIONS?'
    r'|EQUITIES|TELECOM\w*|CONGREGATION|UNION|COUNCIL|BROTHERS|STEEL'
    r'|CONSTRUCTION|CONSULTANTS?)\b',
    re.I,
)

# Committee vocabulary and party-committee acronyms; the FOR-office tail
# catches "SMITH FOR AMERICA" names that carry no committee word at all.
_COMMITTEE_SIGNAL_RE = re.compile(
    r'\b(?:PAC|COMMITTEE|VICTORY|FRIENDS|CITIZENS|FUND|DEMOCRAT'
    r'|REPUBLICAN|SENATE|CONGRESS|ACTION|CAMPAIGN'
    r'|NRSC|NRCC|DCCC|DSCC)\b|FOR (?:CONGRESS|SENATE|AMERICA|GOVERNOR|MAYOR)',
    re.I,
)

# The committee_type labels _classify_committee_names assigns. A row carrying
# one entered this step as a committee: "STEIL FOR WISCONSIN, INC." keeps its
# INC name, so the label (not the name) is what blocks the business tokens.
_POLITICAL_COMMITTEE_TYPES = frozenset({
    'CONGRESSIONAL CAMPAIGN', 'SENATE CAMPAIGN',
    'POLITICAL COMMITTEE', 'POLITICAL ACTION COMMITTEE',
    'PARTY ORGANIZATION',
})

# Strongly-commercial names (banks, trusts, family offices) outrank the
# committee label when the name shows no committee signal.
_STRONG_BUSINESS_RE = re.compile(
    r'\b(?:BANK|TRUST|INVESTMENTS?|CAPITAL|INSURANCE|REALTY|REAL ESTATE'
    r'|HOLDINGS?|EQUITIES|CORPORATION|ENTERPRISES?'
    r'|FAMILY OFFICE|FAMILY TRUST|FAMILY (?:LIMITED )?PARTNERSHIP)\b',
    re.I,
)

# Names that are committees no matter what the filer typed; BELLFORMISSOURI
# is one glued-together filing the generic prefixes miss.
_DEFINITE_COMM_RE = re.compile(
    r'^(?:FRIENDS\s+(?:OF|FOR|TO\s+ELECT)\b'
    r'|BELLFORMISSOURI'
    r'|PEOPLE\s+FOR\b'
    r'|CITIZENS\s+FOR\b'
    r'|TEXANS\s+FOR\b'
    r'|NEVADANS\s+FOR\b'
    r'|ALASKANS\s+FOR\b'
    r'|MONTANANS\s+FOR\b'
    r'|KANSANS\s+FOR\b)',
    re.I,
)

# Org-like patterns for rows filed as individuals with no first/last name.
_NAMELESS_ORG_RE = re.compile(
    r' FOR | PAC| LLC| LP\b| INC| COMMITTEE| COUNCIL| PARTNERS'
    r'| HOLDINGS| INVESTMENT| COMPANY| SERVICES| MEDIA| PROPERTY'
    r'| TRADES| VENTURES| CORPORATION| GROUP| ASSOCIATION'
    r'|^DEMOCRATIC |^REPUBLICAN ',
    re.I,
)


def _blank(s: pd.Series) -> pd.Series:
    """True where the value is missing or whitespace-only."""
    return _norm(s) == ''


def _reclassify_entities(df: pd.DataFrame) -> tuple[int, int]:
    """Fix mistyped entity types in place; returns (n_to_individual, n_to_committee)."""
    name_str = df['contributor_name'].astype(str)
    has_org_keywords = name_str.str.contains(ORG_KEYWORDS, na=False)
    looks_like_person = name_str.str.match(INDIV_NAME_RE, na=False)

    df['_reclass_reason'] = pd.Series(pd.NA, index=df.index, dtype='object')

    n_to_indiv = _reclass_committee_to_individual(df, has_org_keywords, looks_like_person)
    n_to_comm = (
        _reclass_individual_to_committee_org(df, has_org_keywords, looks_like_person)
        + _reclass_individual_nameless_org(df, name_str)
        + _reclass_individual_ghost(df)
        + _reclass_definite_committees(df, name_str)
    )

    # 3-way entity_type: non-individuals split into ORGANIZATION (business
    # signal, no committee signal) vs the COMMITTEE/PAC default
    indiv = df['is_individual']
    names = df['contributor_name'].fillna('').astype(str)
    # rows that entered this step as committees carry a committee_type label
    # (the occupation cleaner sets it); a labeled row needs a strongly
    # commercial name to become ORGANIZATION, a relabeled individual does not
    entered_as_committee = df['committee_type'].isin(_POLITICAL_COMMITTEE_TYPES)
    has_committee_signal = names.str.contains(_COMMITTEE_SIGNAL_RE, na=False)
    strong_business = names.str.contains(_STRONG_BUSINESS_RE, na=False) & ~has_committee_signal
    is_org = (
        ~indiv
        & ~has_committee_signal
        & names.str.contains(_ORG_BUSINESS_RE, na=False)
        & (~entered_as_committee | strong_business)
    )
    df['entity_type'] = np.select(
        [indiv, is_org], ['INDIVIDUAL', 'ORGANIZATION'], default='COMMITTEE/PAC')

    # a name that is ORGANIZATION in any row makes all its non-individual rows
    # ORGANIZATION (COMMITTEE/PAC is only a default, never a positive id)
    _enforce_entity_name_consistency(df)

    df.loc[df['entity_type'] == 'ORGANIZATION', 'occupation_category'] = 'ORGANIZATION'
    df.loc[df['entity_type'] == 'COMMITTEE/PAC', 'occupation_category'] = 'POLITICAL COMMITTEE'
    return n_to_indiv, n_to_comm


def _reclass_committee_to_individual(
    df: pd.DataFrame, has_org_keywords: pd.Series, looks_like_person: pd.Series,
) -> int:
    """Committees whose name is really a person in LASTNAME, FIRST format."""
    should = ~df['is_individual'] & ~has_org_keywords & looks_like_person
    df.loc[should, '_reclass_reason'] = 'committee_to_individual_last_first'
    df.loc[should, 'is_individual'] = True
    return int(should.sum())


def _reclass_individual_to_committee_org(
    df: pd.DataFrame, has_org_keywords: pd.Series, looks_like_person: pd.Series,
) -> int:
    """Individuals with org keywords in a name that doesn't look like a person."""
    mask = df['is_individual'] & has_org_keywords & ~looks_like_person
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_org_keywords'
        df.loc[mask, 'is_individual'] = False
    return n_changed


def _reclass_individual_nameless_org(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Individuals with no first/last name and an org-like name pattern."""
    no_names = _blank(df['contributor_first_name']) & _blank(df['contributor_last_name'])
    mask = df['is_individual'] & no_names & name_str.str.contains(_NAMELESS_ORG_RE, na=False)
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_no_name_org_pattern'
        df.loc[mask, 'is_individual'] = False
    return n_changed


def _reclass_individual_ghost(df: pd.DataFrame) -> int:
    """Individuals with no name, no employer and no occupation are committees."""
    mask = (df['is_individual']
            & _blank(df['contributor_first_name']) & _blank(df['contributor_last_name'])
            & _blank(df['contributor_employer']) & _blank(df['contributor_occupation']))
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_no_identity'
        df.loc[mask, 'is_individual'] = False
    return n_changed


def _reclass_definite_committees(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Names that are always committees (FRIENDS OF, PEOPLE FOR, ...)."""
    mask = df['is_individual'] & name_str.str.contains(_DEFINITE_COMM_RE, na=False)
    n_changed = int(mask.sum())
    if n_changed:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_definite_prefix'
        df.loc[mask, 'is_individual'] = False
        for idx in df.loc[mask].index:
            name = df.at[idx, 'contributor_name']
            if ',' in name:
                df.at[idx, 'contributor_name'] = name.split(',')[0].strip()
            df.at[idx, 'contributor_first_name'] = np.nan
            df.at[idx, 'contributor_last_name'] = np.nan
    return n_changed


def _enforce_entity_name_consistency(df: pd.DataFrame) -> int:
    """Same contributor_name -> same entity_type, ORGANIZATION winning over the COMMITTEE/PAC default; returns rows repointed."""
    org_names = set(df.loc[df['entity_type'] == 'ORGANIZATION',
                           'contributor_name'].dropna())
    if not org_names:
        return 0
    fix = (df['entity_type'] == 'COMMITTEE/PAC') & df['contributor_name'].isin(org_names)
    n_changed = int(fix.sum())
    if n_changed:
        df.loc[fix, 'entity_type'] = 'ORGANIZATION'
    return n_changed


def _restore_reclassified_committees(
    df: pd.DataFrame,
    raw_occ_backup: pd.Series,
    raw_emp_backup: pd.Series,
    log,
) -> None:
    """Restore raw occupation/employer on rows the occupation cleaner emptied as committees before reclassify flipped them back to INDIVIDUAL; mutates df in place."""
    reclass_to_indiv = df['is_individual'] & df['_reclass_reason'].str.startswith(
        'committee_to_individual', na=False)
    if reclass_to_indiv.any():
        raw_occ_for_reclass = raw_occ_backup.loc[reclass_to_indiv]
        raw_emp_for_reclass = raw_emp_backup.loc[reclass_to_indiv]

        has_raw_occ = raw_occ_for_reclass.notna() & (raw_occ_for_reclass.astype(str).str.strip() != '')

        if has_raw_occ.any():
            restored_occ, _ = _normalize_text(
                raw_occ_for_reclass[has_raw_occ], OCCUPATION_NORMALIZE, collapse_retire=True
            )
            is_junk = restored_occ.isin(MISSING_VALUES) | restored_occ.isna()
            valid_restored = restored_occ[~is_junk]

            if len(valid_restored) > 0:
                df.loc[valid_restored.index, 'contributor_occupation'] = valid_restored
                df.loc[valid_restored.index, 'occupation_category'] = _categorize(valid_restored)
                df.loc[valid_restored.index, 'occupation_status'] = 'DISCLOSED'

                needs_fix = valid_restored.index[
                    df.loc[valid_restored.index, 'contributor_occupation'].isin(OCCUPATION_FIXES)
                ]
                if len(needs_fix) > 0:
                    original = df.loc[needs_fix, 'contributor_occupation']
                    fix_occ = {key: value[0] for key, value in OCCUPATION_FIXES.items()}
                    fix_cat = {key: value[1] for key, value in OCCUPATION_FIXES.items()}
                    df.loc[needs_fix, 'contributor_occupation'] = original.map(fix_occ)
                    df.loc[needs_fix, 'occupation_category'] = original.map(fix_cat)

                log(f"  -> restored {len(valid_restored):,} occupations for reclassified committee->individual records")

        # restore employer only where raw had a value and current is empty/generic
        has_raw_emp = raw_emp_for_reclass.notna() & (raw_emp_for_reclass.astype(str).str.strip() != '')
        reclass_no_emp = reclass_to_indiv & (
            df['contributor_employer'].isna()
            | (df['contributor_employer'] == 'NOT DISCLOSED')
        )
        restore_emp = has_raw_emp & reclass_no_emp.loc[has_raw_emp.index]
        if restore_emp.any():
            restored_emp, _ = _normalize_text(
                raw_emp_for_reclass[restore_emp], EMPLOYER_NORMALIZE
            )
            valid_emp = restored_emp[restored_emp.notna() & ~restored_emp.isin(MISSING_VALUES)]
            if len(valid_emp) > 0:
                df.loc[valid_emp.index, 'contributor_employer'] = valid_emp

    # clear committee-only leftover fields on the new individuals
    df.loc[df['is_individual'], 'committee_type'] = 'NOT_APPLICABLE'

    na_status = df['is_individual'] & (df['occupation_status'] == 'NOT_APPLICABLE')
    if na_status.any():
        df.loc[na_status, 'occupation_status'] = 'MISSING'

    # leave remaining gaps as NaN, never fill with NOT DISCLOSED
    new_no_occ = df['is_individual'] & df['contributor_occupation'].isna()
    if new_no_occ.any():
        df.loc[new_no_occ, 'occupation_category'] = pd.NA
        df.loc[new_no_occ, 'occupation_status'] = 'MISSING'

    # re-null truncated 2-char junk the restore brought back
    is_indiv = df['is_individual']
    for col, whitelist in [('contributor_employer', OK_SHORT_EMPLOYERS),
                           ('contributor_occupation', OK_SHORT_OCCUPATIONS)]:
        values = df[col]
        junk = is_indiv & values.notna() & values.str.len().le(2) & ~values.isin(whitelist)
        if junk.any():
            df.loc[junk, col] = np.nan
