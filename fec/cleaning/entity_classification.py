"""Entity type reclassification and name corrections."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm, _indiv_idx
from fec.config import COMM_PATTERNS
from fec.config.constants import LEGAL_SUFFIX_RE as _LEGAL_SUFFIX_RE

_COMMITTEE_IN_NAME_RE = re.compile(
    r'FRIENDS TO ELECT|CITIZENS TO ELECT|COMMITTEE TO ELECT'
    r'|FRIENDS OF |FRIENDS FOR |CITIZENS FOR |PEOPLE FOR '
    r'|FOR CONGRESS|FOR SENATE|FOR U\.?S\.?\s*SENATE'
    r'|FOR AMERICA|FOR GOVERNOR|FOR MAYOR'
    r'|FOR NC\b|FOR NEVADA\b|FOR TEXAS\b|FOR VIRGINIA\b|FOR COLORADO\b'
    r'|FOR NEW YORK\b|FOR MICHIGAN\b|FOR OHIO\b|FOR CALIFORNIA\b'
    r'|FOR GEORGIA\b|FOR FLORIDA\b|FOR ARIZONA\b|FOR PENNSYLVANIA\b'
    r'|FOR WISCONSIN\b|FOR MISSOURI\b|FOR INDIANA\b|FOR MARYLAND\b'
    r'|FOR MINNESOTA\b|FOR WASHINGTON\b|FOR ILLINOIS\b|FOR OKLAHOMA\b'
    r'|VICTORY FUND|ACTION FUND|LEADERSHIP PAC|LEADERSHIP FUND'
    r'|BELLFORMISSOURI'
    r'|4\s*CONGRESS|4\s*UTAH|4\s*SENATE'
    r'|CAMPAIGN$'
    r'|\.COM$'
    r'|ELECT\w+\.COM',
    re.IGNORECASE,
)

_BUSINESS_SUFFIX_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bL\.L\.C\b|\bL\.L\.P\b'
    r'|\bINC\b|\bCORP\b|\bLTD\b|\bP\.?L\.?L\.?C\b'
    r'|\bP\.?C\.?\b|\bP\.?A\.?\b',
    re.IGNORECASE
)

_GENERIC_EMP_OCC = frozenset({
    'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', 'NOT DISCLOSED', 'HOMEMAKER',
    'STUDENT', 'REAL ESTATE', 'FINANCE', 'SALES', 'CONSULTING', 'MANAGEMENT',
    'INSURANCE', 'MARKETING', 'BANKING', 'GOVERNMENT', 'EDUCATION',
    'CONSTRUCTION', 'ACCOUNTING', 'TECHNOLOGY', 'ENGINEERING', 'VOLUNTEER',
    'CASHIER', 'NURSE', 'HEALTHCARE', 'ENTREPRENEUR', 'INDEPENDENT CONTRACTOR',
    'FREELANCE', 'COMMUNITY VOLUNTEER', 'EMPLOYED', 'INVESTOR',
    'FUNDRAISING CONSULTANT', 'INSURANCE AGENT', 'NON-PROFIT VOLUNTEER',
    'BUSINESS',
})

_NAME_CORRECTIONS = {
    "CHALME'', RAYMOND": "CHALME, RAYMOND",
    # Hand-verified org names the LAST, FIRST parse flipped (auto-unflipping comma'd
    # org names is unsafe); keys must stay in sync with data/database/entity_overrides.csv.
    "BANK, FIRST CENTRAL": "FIRST CENTRAL SAVINGS BANK",
    "CAPITAL, WHITE": "WHITE LAKE REAL ESTATE CAPITAL LLC",
    "KAHAN TRUST, DAVID": "DAVID KAHAN TRUST",
}

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
        df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
        df.loc[hits, 'is_individual'] = False
        df.loc[hits, 'contributor_first_name'] = np.nan
        df.loc[hits, 'contributor_last_name'] = np.nan
        df.loc[hits, 'occupation_category'] = 'POLITICAL COMMITTEE'
        df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
        df.loc[hits, 'contributor_occupation'] = np.nan
        df.loc[hits, 'contributor_employer'] = np.nan

        for idx in hits:
            name = str(df.at[idx, 'contributor_name'])
            matched = False
            for comm_type, pattern in COMM_PATTERNS:
                if pattern.search(name):
                    df.at[idx, 'committee_type'] = comm_type
                    matched = True
                    break
            if not matched:
                df.at[idx, 'committee_type'] = 'POLITICAL COMMITTEE'

    return df, n_fixed


def fix_misclassified_business_entities(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Reclassify individuals that are actually business entities."""
    indiv_idx = _indiv_idx(df)
    names = _norm(df.loc[indiv_idx, 'contributor_name'])
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').astype(str).str.strip()
    emp = df.loc[indiv_idx, 'contributor_employer'].fillna('').astype(str).str.strip().str.upper()

    has_suffix = names.str.contains(_BUSINESS_SUFFIX_RE.pattern, na=False, regex=True)

    no_comma = ~names.str.contains(',', na=False)
    no_first = (first_names == '') | (first_names.str.upper() == 'NAN')
    empty_emp = emp.isin({'', 'NAN', 'NONE'})
    looks_like_org = no_comma & no_first & empty_emp

    hits = indiv_idx[has_suffix | looks_like_org]
    n_fixed = len(hits)

    if n_fixed:
        df.loc[hits, 'entity_type'] = 'COMMITTEE/PAC'
        df.loc[hits, 'is_individual'] = False
        df.loc[hits, 'contributor_first_name'] = np.nan
        df.loc[hits, 'contributor_last_name'] = np.nan
        df.loc[hits, 'occupation_category'] = 'ORGANIZATION'
        df.loc[hits, 'occupation_status'] = 'NOT_APPLICABLE'
        df.loc[hits, 'contributor_occupation'] = np.nan
        df.loc[hits, 'contributor_employer'] = np.nan

    return df, n_fixed


def normalize_business_names(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip legal suffixes (LLC, LLP, INC) from non-individual contributor names."""
    mask = df['entity_type'] != 'INDIVIDUAL'
    names = df.loc[mask, 'contributor_name'].fillna('')
    cleaned = names.str.replace(_LEGAL_SUFFIX_RE, '', regex=True).str.strip()
    changed = (cleaned != names) & (cleaned != '')
    n_fixed = changed.sum()
    if n_fixed:
        df.loc[changed[changed].index, 'contributor_name'] = cleaned[changed]
    return df, int(n_fixed)


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


def apply_name_corrections(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply manual name corrections."""
    n_fixed = 0
    for old, new in _NAME_CORRECTIONS.items():
        mask = df['contributor_name'] == old
        count = mask.sum()
        if count:
            df.loc[mask, 'contributor_name'] = new
            n_fixed += count
    return df, n_fixed


def fix_double_apostrophes(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Collapse doubled apostrophes in name fields (CHALME'' becomes CHALME')."""
    n_fixed = 0
    for col in ['contributor_name', 'contributor_first_name', 'contributor_last_name']:
        if col in df.columns:
            vals = df[col].fillna('')
            has_double = vals.str.contains("''", na=False)
            if has_double.any():
                df.loc[has_double, col] = vals[has_double].str.replace("''", "'", regex=False)
                if col == 'contributor_name':
                    n_fixed += int(has_double.sum())

    return df, n_fixed


def fix_null_last_name(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Strip artifact 'NULL, ' prefixes; a real employer plus occupation means NULL is a true surname."""
    indiv_idx = _indiv_idx(df)
    names = df.loc[indiv_idx, 'contributor_name'].fillna('')
    null_mask = indiv_idx[names.str.startswith('NULL, ')]
    n_fixed = 0

    STATUS = {'RETIRED', 'SELF-EMPLOYED', 'NOT EMPLOYED', 'NOT DISCLOSED', '', 'nan', 'NAN'}

    if len(null_mask):
        for idx in null_mask:
            emp = str(df.at[idx, 'contributor_employer'] or '').strip()
            occ = str(df.at[idx, 'contributor_occupation'] or '').strip()

            if emp and emp not in STATUS and occ and occ not in STATUS:
                df.at[idx, 'contributor_last_name'] = 'NULL'
                first = df.at[idx, 'contributor_name'].replace('NULL, ', '', 1).strip()
                df.at[idx, 'contributor_first_name'] = first
                df.at[idx, 'contributor_name'] = f"NULL, {first}"
            else:
                name = df.at[idx, 'contributor_name']
                first = name.replace('NULL, ', '', 1).strip()
                df.at[idx, 'contributor_name'] = first
                df.at[idx, 'contributor_last_name'] = np.nan
                n_fixed += 1

    return df, n_fixed


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


def fix_fullname_in_both_fields(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Split a full name duplicated into both first_name and last_name."""
    indiv_idx = _indiv_idx(df)
    first_names = df.loc[indiv_idx, 'contributor_first_name'].fillna('').str.strip()
    last_names = df.loc[indiv_idx, 'contributor_last_name'].fillna('').str.strip()

    mask = (first_names.str.upper() == last_names.str.upper()) & (first_names != '') & first_names.str.contains(' ')
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
