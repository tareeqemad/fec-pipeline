"""Entity type reclassification and name corrections."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.cleaning.name_rules import EXACT_NAME_CORRECTIONS, ROW_NAME_CORRECTIONS
from fec.config.constants import STATUS_CATEGORIES
from fec.config.not_employers import LEGAL_SUFFIX_RE

_COMMITTEE_IN_NAME_RE = re.compile(
    r'(?:FRIENDS|CITIZENS|COMMITTEE) TO ELECT'
    r'|(?:FRIENDS OF|FRIENDS FOR|CITIZENS FOR|PEOPLE FOR) '
    r'|FOR (?:CONGRESS|SENATE|U\.?S\.?\s*SENATE|AMERICA|GOVERNOR|MAYOR)'
    r'|FOR (?:NC|NEVADA|TEXAS|VIRGINIA|COLORADO|NEW YORK|MICHIGAN|OHIO|CALIFORNIA'
    r'|GEORGIA|FLORIDA|ARIZONA|PENNSYLVANIA|WISCONSIN|MISSOURI|INDIANA|MARYLAND'
    r'|MINNESOTA|WASHINGTON|ILLINOIS|OKLAHOMA)\b'
    r'|(?:VICTORY|ACTION|LEADERSHIP) FUND|LEADERSHIP PAC'
    r'|BELLFORMISSOURI'
    r'|4\s*CONGRESS|4\s*UTAH|4\s*SENATE'
    r'|CAMPAIGN$'
    r'|\.COM$'
    r'|ELECT\w+\.COM',
    re.IGNORECASE,
)

_BUSINESS_SUFFIX_RE = re.compile(
    r'\b(?:LLC|LLP|L\.L\.C|L\.L\.P|INC|CORP|LTD|P\.?L\.?L\.?C)\b'
    r'|\bP\.?C\.?\b|\bP\.?A\.?\b',
    re.IGNORECASE
)

_GENERIC_EMP_OCC = STATUS_CATEGORIES | frozenset({
    'NOT DISCLOSED', 'REAL ESTATE', 'FINANCE', 'SALES', 'CONSULTING', 'MANAGEMENT',
    'INSURANCE', 'MARKETING', 'BANKING', 'GOVERNMENT', 'EDUCATION',
    'CONSTRUCTION', 'ACCOUNTING', 'TECHNOLOGY', 'ENGINEERING', 'VOLUNTEER',
    'CASHIER', 'NURSE', 'HEALTHCARE', 'ENTREPRENEUR', 'INDEPENDENT CONTRACTOR',
    'FREELANCE', 'COMMUNITY VOLUNTEER', 'EMPLOYED', 'INVESTOR',
    'FUNDRAISING CONSULTANT', 'INSURANCE AGENT', 'NON-PROFIT VOLUNTEER',
    'BUSINESS',
})

# Known credentials only, so real two-letter last names are not mistaken for one.
_CREDENTIAL_RE = re.compile(
    r'^\s*(?:'
    r'M\s*D'
    r'|D\s*O'
    r'|D\s*V\s*M'
    r'|D\s*D\s*S'
    r'|D\s*M\s*D'
    r'|D\s*P\s*M'
    r'|D\s*C'
    r'|O\s*D'
    r'|PH\s*D'
    r'|J\s*D'
    r'|M\s*B\s*A'
    r'|R\s*N'
    r'|R\s*PH'
    r'|PHARM\s*D'
    r'|C\s*P\s*A'
    r'|ESQ'
    r'|P\s*E'
    r')\s*$',
    re.IGNORECASE,
)

# 'LAST, CREDENTIAL, FIRST'
_THREE_PART_NAME_RE = re.compile(r'^([^,]+),\s*([^,]+),\s*(.+)$')


# reclassify given rows as a committee/PAC and clear individual fields
def _mark_as_committee(df: pd.DataFrame, hits, category: str) -> None:
    df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
    df.loc[hits, 'is_individual'] = False
    df.loc[hits, 'contributor_first_name'] = np.nan
    df.loc[hits, 'contributor_last_name'] = np.nan
    df.loc[hits, 'occupation_category'] = category
    df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[hits, 'contributor_occupation'] = np.nan
    df.loc[hits, 'contributor_employer'] = np.nan


# reclassify individuals whose name matches committee patterns
def fix_remaining_misclassified(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reclassify individuals whose name matches committee patterns as COMMITTEE/PAC."""
    indiv_idx = _indiv_idx(df)
    last_names = _norm(df.loc[indiv_idx, 'contributor_last_name'])
    names = _norm(df.loc[indiv_idx, 'contributor_name'])

    hits = indiv_idx[
        last_names.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
        | names.str.contains(_COMMITTEE_IN_NAME_RE.pattern, na=False, regex=True)
    ]
    n_fixed = len(hits)

    if n_fixed:
        _mark_as_committee(df, hits, 'POLITICAL COMMITTEE')

    return df, n_fixed


# reclassify individuals that are actually business entities
def fix_misclassified_business_entities(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reclassify individuals that are actually business entities."""
    indiv_idx = _indiv_idx(df)
    names = _norm(df.loc[indiv_idx, 'contributor_name'])
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').astype(str).str.strip()
    emp = df.loc[indiv_idx, 'contributor_employer'].fillna('').astype(str).str.strip().str.upper()

    has_suffix = names.str.contains(_BUSINESS_SUFFIX_RE.pattern, na=False, regex=True)

    no_comma = ~names.str.contains(',', na=False, regex=False)
    no_first = (first_names == '') | (first_names.str.upper() == 'NAN')
    empty_emp = emp.isin({'', 'NAN', 'NONE'})
    looks_like_org = no_comma & no_first & empty_emp

    hits = indiv_idx[has_suffix | looks_like_org]
    n_fixed = len(hits)

    if n_fixed:
        _mark_as_committee(df, hits, 'ORGANIZATION')
    return df, n_fixed


# strip legal suffixes from non-individual contributor names
def normalize_business_names(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip legal suffixes (LLC, LLP, INC) from non-individual contributor names."""
    mask = df['entity_type'] != 'INDIVIDUAL'
    names = df.loc[mask, 'contributor_name'].fillna('')
    cleaned = names.str.replace(LEGAL_SUFFIX_RE, '', regex=True).str.strip()
    changed = (cleaned != names) & (cleaned != '')
    n_fixed = changed.sum()
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, int(n_fixed)


# fix occupation when it duplicates a non-generic employer name
def fix_employer_equals_occupation(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """When a non-generic company name sits in both employer and occupation, fix occupation."""
    indiv_idx = _indiv_idx(df)
    emp = _norm(df.loc[indiv_idx, 'contributor_employer'])
    occ = _norm(df.loc[indiv_idx, 'contributor_occupation'])

    same = indiv_idx[(emp == occ) & (emp != '') & ~emp.isin(_GENERIC_EMP_OCC)]
    n_fixed = len(same)

    if n_fixed:
        for idx in same:
            val = df.at[idx, 'contributor_occupation']
            first_name = (df.at[idx, 'contributor_first_name'] or '').upper()
            last_name = (df.at[idx, 'contributor_last_name'] or '').upper()
            val_upper = (val or '').upper()

            if first_name and last_name and (first_name in val_upper or last_name in val_upper):
                # their own name in both fields: self-employed
                df.at[idx, 'contributor_occupation'] = 'SELF-EMPLOYED'
                df.at[idx, 'occupation_category'] = 'SELF-EMPLOYED'
                df.at[idx, 'occupation_status'] = 'DISCLOSED'
            else:
                # a company in both fields: occupation unknown
                df.at[idx, 'contributor_occupation'] = np.nan
                df.at[idx, 'occupation_category'] = pd.NA
                df.at[idx, 'occupation_status'] = 'MISSING'

    return df, n_fixed


# apply manual name corrections, exact and per-row
def apply_name_corrections(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply manual name corrections."""
    corrected = df['contributor_name'].map(EXACT_NAME_CORRECTIONS)
    if 'sub_id' in df.columns:
        by_row = df['sub_id'].astype(str).map(ROW_NAME_CORRECTIONS)
        corrected = by_row.combine_first(corrected)

    changed = corrected.notna() & corrected.ne(df['contributor_name'])
    for index, new_name in corrected[changed].items():
        df.at[index, 'contributor_name'] = new_name
        if df.at[index, 'entity_type'] == 'INDIVIDUAL' and ',' in new_name:
            last, first = (part.strip() for part in new_name.split(',', 1))
            df.at[index, 'contributor_first_name'] = first
            df.at[index, 'contributor_last_name'] = last

    return df, int(changed.sum())


# collapse doubled apostrophes in name fields
def fix_double_apostrophes(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Collapse doubled apostrophes in name fields (CHALME'' becomes CHALME')."""
    n_fixed = 0
    for col in ['contributor_name', 'contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            vals = df[col].fillna('')
            has_double = vals.str.contains("''", na=False, regex=False)
            if has_double.any():
                df.loc[has_double, col] = vals[has_double].str.replace("''", "'", regex=False)
                if col == 'contributor_name':
                    n_fixed += int(has_double.sum())

    return df, n_fixed


# remove periods from initials and titles in contributor names
def normalize_name_periods(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Remove periods from initials and titles in contributor_name, all records."""
    names = df['contributor_name'].fillna('')

    cleaned = names.str.replace(r'\b([A-Z])\.', r'\1 ', regex=True)
    cleaned = cleaned.str.replace(r'\b(MR|MRS|MS|DR|JR|SR|II|III|IV)\.', r'\1', regex=True)
    cleaned = cleaned.str.replace(r'\s+', ' ', regex=True).str.strip()

    changed = (cleaned != names) & (cleaned != '')
    n_fixed = int(changed.sum())
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, n_fixed


# drop a credential wedged into 'LAST, CREDENTIAL, FIRST'
def fix_credential_in_name(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Drop a credential wedged in 'LAST, CREDENTIAL, FIRST'; must run after normalize_name_periods."""
    indiv_idx = _indiv_idx(df)
    if not len(indiv_idx):
        return df, 0

    names = df.loc[indiv_idx, 'contributor_name'].fillna('').astype(str)
    # exactly two commas; anything else is a different structure, leave it alone
    candidates = indiv_idx[names.str.count(',') == 2]
    if not len(candidates):
        return df, 0

    n_fixed = 0

    for idx in candidates:
        name = df.at[idx, 'contributor_name']
        if not isinstance(name, str):
            continue
        match = _THREE_PART_NAME_RE.match(name)
        if not match:
            continue

        last_part = match.group(1).strip()
        credential_part = match.group(2).strip().rstrip('.').strip()
        first_part = match.group(3).strip()

        if not _CREDENTIAL_RE.match(credential_part):
            continue

        first_clean = first_part.rstrip('.').strip()
        last_clean = last_part.rstrip('.').strip()
        if not first_clean or not last_clean:
            continue

        df.at[idx, 'contributor_name'] = f'{last_clean}, {first_clean}'
        df.at[idx, 'contributor_first_name'] = first_clean
        df.at[idx, 'contributor_last_name'] = last_clean
        n_fixed += 1

    return df, n_fixed


# split a full name duplicated into first and last
def fix_fullname_in_both_fields(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Split a full name duplicated into both first_name and last_name."""
    indiv_idx = _indiv_idx(df)
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').str.strip()
    last_names = df.loc[indiv_idx, 'contributor_last_name'].fillna('').str.strip()

    mask = (
        (first_names.str.upper() == last_names.str.upper())
        & (first_names != '')
        & first_names.str.contains(' ', regex=False)
    )
    hits = indiv_idx[mask]
    n_fixed = len(hits)

    if n_fixed:
        for idx in hits:
            full = df.at[idx, 'contributor_first_name'].strip()
            parts = full.split()
            if len(parts) >= 2:
                new_first = parts[0]
                new_last = ' '.join(parts[1:])
                df.at[idx, 'contributor_first_name'] = new_first
                df.at[idx, 'contributor_last_name'] = new_last
                df.at[idx, 'contributor_name'] = f"{new_last}, {new_first}"

    return df, n_fixed
