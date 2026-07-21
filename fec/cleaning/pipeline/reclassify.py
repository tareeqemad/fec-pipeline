"""cleaning/pipeline/reclassify.py — entity-type reclassification.

Decide INDIVIDUAL vs ORGANIZATION vs COMMITTEE/PAC for each record, fixing rows
the filer mistyped (a person filed as a committee, or vice-versa), and restore
the occupation/employer of records flipped committee → individual.
"""
from __future__ import annotations

import re
from typing import Tuple

import numpy as np
import pandas as pd

from fec.config import ORG_KEYWORDS, INDIV_NAME_RE, MISSING_VALUES


def _restore_reclassified_committees(
    df: pd.DataFrame,
    raw_occ_backup: pd.Series,
    raw_emp_backup: pd.Series,
    log,
) -> None:
    """
    Restore and clean up occupation/employer for records flipped from
    committee → individual in step 8.

    Step 7 cleared occupation for all records then marked as committee,
    but step 8 reclassified some back to INDIVIDUAL. This helper:
      8b. Restores raw occupation (normalized + typo-fixed + categorized)
          and raw employer (where current is empty/generic).
      8c. Clears committee-only CAMPAIGN/COMMITTEE employer and resets
          committee_type / occupation_status for the new individuals.
      8d. Re-nulls truncated 2-char junk that snuck back in via the restore.

    Mutates df in place.
    """
    from fec.cleaning.occupations import _normalize_text, _categorize
    from fec.config import (
        OCCUPATION_NORMALIZE, OCCUPATION_FIXES, EMPLOYER_NORMALIZE,
    )
    from fec.config.constants import OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS

    # 8b. Restore occupation/employer for records flagged committee→individual
    reclass_to_indiv = df['is_individual'] & (
        df.get('_reclass_reason', pd.Series(dtype='object')).notna()
        & df['_reclass_reason'].str.startswith('committee_to_individual', na=False)
    )
    if reclass_to_indiv.any():
        raw_occ_for_reclass = raw_occ_backup.loc[reclass_to_indiv]
        raw_emp_for_reclass = raw_emp_backup.loc[reclass_to_indiv]

        has_raw_occ = raw_occ_for_reclass.notna() & (raw_occ_for_reclass.astype(str).str.strip() != '')

        if has_raw_occ.any():
            restored_occ, _ = _normalize_text(
                raw_occ_for_reclass[has_raw_occ], OCCUPATION_NORMALIZE, collapse_retire=True
            )
            # Check if normalized value is junk → leave as NaN
            is_junk = restored_occ.isin(MISSING_VALUES) | restored_occ.isna()
            valid_restored = restored_occ[~is_junk]

            if len(valid_restored) > 0:
                df.loc[valid_restored.index, 'contributor_occupation'] = valid_restored
                df.loc[valid_restored.index, 'occupation_category'] = _categorize(valid_restored)
                df.loc[valid_restored.index, 'occupation_status'] = 'DISCLOSED'

                # Apply typo fixes
                needs_fix = valid_restored.index[
                    df.loc[valid_restored.index, 'contributor_occupation'].isin(OCCUPATION_FIXES)
                ]
                if len(needs_fix) > 0:
                    orig = df.loc[needs_fix, 'contributor_occupation']
                    fix_occ = {k: v[0] for k, v in OCCUPATION_FIXES.items()}
                    fix_cat = {k: v[1] for k, v in OCCUPATION_FIXES.items()}
                    df.loc[needs_fix, 'contributor_occupation'] = orig.map(fix_occ)
                    df.loc[needs_fix, 'occupation_category'] = orig.map(fix_cat)

                log(f"  → restored {len(valid_restored):,} occupations for reclassified committee→individual records")

        # Restore employer where raw had a value and current is empty/generic
        has_raw_emp = raw_emp_for_reclass.notna() & (raw_emp_for_reclass.astype(str).str.strip() != '')
        reclass_no_emp = reclass_to_indiv & (
            df['contributor_employer'].isna()
            | df['contributor_employer'].isin(['CAMPAIGN/COMMITTEE', 'NOT DISCLOSED'])
        )
        restore_emp = has_raw_emp & reclass_no_emp.loc[has_raw_emp.index]
        if restore_emp.any():
            restored_emp, _ = _normalize_text(
                raw_emp_for_reclass[restore_emp], EMPLOYER_NORMALIZE
            )
            valid_emp = restored_emp[restored_emp.notna() & ~restored_emp.isin(MISSING_VALUES)]
            if len(valid_emp) > 0:
                df.loc[valid_emp.index, 'contributor_employer'] = valid_emp

    # 8c. Clean up committee-only leftover fields on the new individuals
    fake_emp = df['is_individual'] & (df['contributor_employer'] == 'CAMPAIGN/COMMITTEE')
    if fake_emp.any():
        df.loc[fake_emp, 'contributor_employer'] = np.nan
        log(f"  → cleared {int(fake_emp.sum()):,} CAMPAIGN/COMMITTEE employers for reclassified individuals")

    if 'committee_type' in df.columns:
        df.loc[df['is_individual'] == True, 'committee_type'] = 'NOT_APPLICABLE'

    na_status = df['is_individual'] & (df['occupation_status'] == 'NOT_APPLICABLE')
    if na_status.any():
        df.loc[na_status, 'occupation_status'] = 'MISSING'

    # Leave remaining gaps as NaN — don't fill with "NOT DISCLOSED"
    new_no_occ = df['is_individual'] & df['contributor_occupation'].isna()
    if new_no_occ.any():
        df.loc[new_no_occ, 'occupation_category'] = pd.NA
        df.loc[new_no_occ, 'occupation_status'] = 'MISSING'

    # 8d. Re-null truncated 2-char junk that snuck back in via the restore
    is_indiv = df['is_individual']
    for col, whitelist in [('contributor_employer', OK_SHORT_EMPLOYERS),
                           ('contributor_occupation', OK_SHORT_OCCUPATIONS)]:
        val = df[col]
        junk = is_indiv & val.notna() & val.str.len().le(2) & ~val.isin(whitelist)
        if junk.any():
            df.loc[junk, col] = np.nan


# Business vs political-committee name signals — used to split non-individual
# donors into ORGANIZATION (companies) vs COMMITTEE/PAC (political committees).
_ORG_BUSINESS_RE = (
    r'\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|LLP|LP|HOLDINGS?|ENTERPRISES?'
    r'|VENTURES?|PROPERTIES|PROPERTY|REALTY|REAL ESTATE|MANAGEMENT|PARTNERS'
    r'|PARTNERSHIP|CAPITAL|GROUP|ASSOCIATES|COMPANY|INDUSTRIES|INVESTMENTS?'
    r'|BANK|INSURANCE|FOUNDATION|TRUST|MEDIA|DEVELOPMENT|LENDING|ACQUISITIONS?'
    r'|EQUITIES|TELECOM\w*|CONGREGATION|UNION|COUNCIL|BROTHERS|STEEL|MOTORS'
    r'|AUTOMOTIVE|AGENCY|SYSTEMS|SOLUTIONS|TECHNOLOG\w*|FARMS?|DAIRY'
    r'|CONSTRUCTION|CONSULTANTS?)\b'
)
_COMMITTEE_SIGNAL_RE = (
    r'\b(?:PAC|COMMITTEE|VICTORY|FRIENDS|CITIZENS|FUND|PARTY|DEMOCRAT'
    r'|REPUBLICAN|SENATE|CONGRESS|ACTION|LEADERSHIP|CAMPAIGN'
    r'|NRSC|NRCC|DCCC|DSCC|DNC|RNC)\b|FOR (?:CONGRESS|SENATE|AMERICA|GOVERNOR|MAYOR)'
)

# Occupations that mark a record as a political committee, not a business. A
# candidate campaign like "STEIL FOR WISCONSIN, INC." has "INC" in its name but
# is a committee — its occupation already says so. Used to keep such rows out of
# the ORGANIZATION bucket (the "FOR <STATE>" pattern isn't in _COMMITTEE_SIGNAL_RE).
_POLITICAL_OCCUPATIONS = frozenset({
    'CONGRESSIONAL CAMPAIGN', 'SENATE CAMPAIGN',
    'POLITICAL COMMITTEE', 'POLITICAL ACTION COMMITTEE',
    'PARTY ORGANIZATION',
})

# Strongly-commercial name signals that virtually never appear in a real
# political committee's name. When one of these is present (and there's no
# committee signal), the record is a business — even if its occupation field
# was mislabeled "POLITICAL COMMITTEE". Catches banks, trusts, family offices
# and investment firms wrongly bucketed as committees (e.g. CHAIN BRIDGE BANK,
# KOHN FAMILY TRUST, RANGER CAPITAL CORPORATION).
_STRONG_BUSINESS_RE = (
    r'\b(?:BANK|TRUST|INVESTMENTS?|CAPITAL|INSURANCE|REALTY|REAL ESTATE'
    r'|HOLDINGS?|BROKERAGE|EQUITIES|CORPORATION|ENTERPRISES?'
    r'|FAMILY OFFICE|FAMILY TRUST|FAMILY (?:LIMITED )?PARTNERSHIP)\b'
)


def _enforce_entity_name_consistency(df: pd.DataFrame) -> int:
    """Same contributor_name → same entity_type. ORGANIZATION wins over the
    COMMITTEE/PAC default. Returns rows repointed to ORGANIZATION."""
    if 'entity_type' not in df.columns or 'contributor_name' not in df.columns:
        return 0
    org_names = set(df.loc[df['entity_type'] == 'ORGANIZATION',
                           'contributor_name'].dropna())
    if not org_names:
        return 0
    fix = (df['entity_type'] == 'COMMITTEE/PAC') & df['contributor_name'].isin(org_names)
    n = int(fix.sum())
    if n:
        df.loc[fix, 'entity_type'] = 'ORGANIZATION'
    return n


def _reclassify_entities(df: pd.DataFrame) -> Tuple[int, int]:
    """
    Find records marked as committee that are actually individuals,
    AND records marked as individual that are actually organizations.

    Modifies df in-place. Returns (n_to_individual, n_to_committee).
    """
    name_str = df['contributor_name'].astype(str)
    has_org_keywords = name_str.str.contains(ORG_KEYWORDS, na=False)
    looks_like_person = name_str.str.match(INDIV_NAME_RE.pattern, na=False)

    if '_reclass_reason' not in df.columns:
        df['_reclass_reason'] = pd.Series(pd.NA, index=df.index, dtype='object')

    n_to_indiv = _reclass_committee_to_individual(df, name_str, has_org_keywords, looks_like_person)
    n1 = _reclass_individual_to_committee_org(df, has_org_keywords, looks_like_person)
    n2 = _reclass_individual_nameless_org(df, name_str)
    n3 = _reclass_individual_ghost(df)
    n4 = _reclass_definite_committees(df, name_str)

    # 3-way entity_type. Non-individuals split into ORGANIZATION (a business
    # signal in the name and NO committee signal) vs COMMITTEE/PAC (political
    # committees). Companies like "EATON STEEL CORPORATION" no longer get
    # mislabeled as a political committee.
    indiv = df['is_individual'].fillna(False).astype(bool)
    nm = df['contributor_name'].fillna('').astype(str)
    # The political signal lives in committee_type by this step (step 7 moves the
    # committee occupation there and clears contributor_occupation); check both.
    ct = df.get('committee_type', pd.Series('', index=df.index)).fillna('')
    occ = df['contributor_occupation'].fillna('')
    is_political = ct.isin(_POLITICAL_OCCUPATIONS) | occ.isin(_POLITICAL_OCCUPATIONS)
    has_comm_sig = nm.str.contains(_COMMITTEE_SIGNAL_RE, case=False, regex=True, na=False)
    # A strong commercial name overrides a mislabeled political occupation.
    strong_business = nm.str.contains(_STRONG_BUSINESS_RE, case=False, regex=True, na=False) & ~has_comm_sig
    is_org = (
        ~indiv
        & ~has_comm_sig
        & nm.str.contains(_ORG_BUSINESS_RE, case=False, regex=True, na=False)
        & (~is_political | strong_business)
    )
    df['entity_type'] = np.select(
        [indiv, is_org], ['INDIVIDUAL', 'ORGANIZATION'], default='COMMITTEE/PAC')

    # Per-name consistency: ORGANIZATION is positively identified (a business
    # signal in the name); COMMITTEE/PAC is the default catch-all. So if a name
    # is ORGANIZATION in any row, treat all its non-individual rows the same —
    # the same company can't be a committee in one filing and a business in
    # another (e.g. KRAFT GROUP, TRILOGY INTERACTIVE).
    _enforce_entity_name_consistency(df)

    if 'occupation_category' in df.columns:
        df.loc[df['entity_type'] == 'ORGANIZATION',  'occupation_category'] = 'ORGANIZATION'
        df.loc[df['entity_type'] == 'COMMITTEE/PAC', 'occupation_category'] = 'POLITICAL COMMITTEE'
    return n_to_indiv, n1 + n2 + n3 + n4


def _reclass_committee_to_individual(
    df: pd.DataFrame, name_str: pd.Series,
    has_org_keywords: pd.Series, looks_like_person: pd.Series,
) -> int:
    """Reclassify committees that are actually individuals (LASTNAME, FIRST format or compound name)."""
    is_committee = ~df['is_individual']
    no_org = ~has_org_keywords
    not_political = ~df['contributor_occupation'].isin(_POLITICAL_OCCUPATIONS)

    # A) Standard: LASTNAME, FIRST format
    should_a = is_committee & no_org & looks_like_person & not_political

    # B) Compound names: short name + personal occupation
    has_personal_occ = (
        df['contributor_occupation'].notna() & not_political
        & (df['contributor_occupation'] != 'POLITICAL COMMITTEE')
    )
    word_count = name_str.str.split().str.len()
    short_name = word_count.le(3) & word_count.ge(1)
    should_b = is_committee & no_org & ~looks_like_person & has_personal_occ & short_name

    df.loc[should_a, '_reclass_reason'] = 'committee_to_individual_last_first'
    df.loc[should_b, '_reclass_reason'] = 'committee_to_individual_compound_personal_occ'
    df.loc[should_a | should_b, 'is_individual'] = True

    return int((should_a | should_b).sum())


def _reclass_individual_to_committee_org(
    df: pd.DataFrame, has_org_keywords: pd.Series, looks_like_person: pd.Series,
) -> int:
    """Reclassify individuals with org keywords that don't look like people."""
    mask = df['is_individual'] & has_org_keywords & ~looks_like_person
    n = int(mask.sum())
    if n:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_org_keywords'
        df.loc[mask, 'is_individual'] = False
    return n


def _reclass_individual_nameless_org(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Reclassify individuals with no first/last + org-like name patterns."""
    is_indiv = df['is_individual']
    no_first = df['contributor_first_name'].isna() | df['contributor_first_name'].fillna('').str.strip().eq('')
    no_last = df['contributor_last_name'].isna() | df['contributor_last_name'].fillna('').str.strip().eq('')
    has_no_names = no_first & no_last

    nameless_org_re = re.compile(
        r' FOR | PAC| LLC| LP\b| INC| COMMITTEE| COUNCIL| PARTNERS'
        r'| HOLDINGS| INVESTMENT| COMPANY| SERVICES| MEDIA| PROPERTY'
        r'| TRADES| VENTURES| CORPORATION| GROUP| ASSOCIATION'
        r'|^DEMOCRATIC |^REPUBLICAN ',
        re.I,
    )
    mask = is_indiv & has_no_names & name_str.str.contains(nameless_org_re, na=False)
    n = int(mask.sum())
    if n:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_no_name_org_pattern'
        df.loc[mask, 'is_individual'] = False
    return n


def _reclass_individual_ghost(df: pd.DataFrame) -> int:
    """Reclassify individuals with no name, no employer, no occupation → committee."""
    is_indiv = df['is_individual']
    no_first = df['contributor_first_name'].isna() | df['contributor_first_name'].fillna('').str.strip().eq('')
    no_last = df['contributor_last_name'].isna() | df['contributor_last_name'].fillna('').str.strip().eq('')
    no_emp = df['contributor_employer'].isna() | df['contributor_employer'].fillna('').str.strip().eq('')
    no_occ = df['contributor_occupation'].isna() | df['contributor_occupation'].fillna('').str.strip().eq('')
    mask = is_indiv & no_first & no_last & no_emp & no_occ
    n = int(mask.sum())
    if n:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_no_identity'
        df.loc[mask, 'is_individual'] = False
    return n


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


def _reclass_definite_committees(df: pd.DataFrame, name_str: pd.Series) -> int:
    """Reclassify names that are ALWAYS committees (FRIENDS OF, PEOPLE FOR, etc.)."""
    mask = df['is_individual'] & name_str.str.contains(_DEFINITE_COMM_RE.pattern, na=False, regex=True)
    n = int(mask.sum())
    if n:
        df.loc[mask, '_reclass_reason'] = 'individual_to_committee_definite_prefix'
        df.loc[mask, 'is_individual'] = False
        for idx in df.loc[mask].index:
            name = df.at[idx, 'contributor_name']
            if ',' in name:
                df.at[idx, 'contributor_name'] = name.split(',')[0].strip()
            df.at[idx, 'contributor_first_name'] = np.nan
            df.at[idx, 'contributor_last_name'] = np.nan
    return n
