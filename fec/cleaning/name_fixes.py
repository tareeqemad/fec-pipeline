"""Name corrections: manual fixes, apostrophes, periods, credentials and duplicated names."""
from __future__ import annotations

import re

import pandas as pd

from fec.cleaning._helpers import _indiv_idx
from fec.cleaning.names import EXACT_NAME_CORRECTIONS, ROW_NAME_CORRECTIONS


# Known credentials only, so two-letter surnames stay.
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


def apply_name_corrections(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Apply manual name corrections."""
    corrected = df['contributor_name'].map(EXACT_NAME_CORRECTIONS)
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
    """Remove embedded credentials after normalize_name_periods."""
    indiv_idx = _indiv_idx(df)
    if not len(indiv_idx):
        return df, 0

    names = df.loc[indiv_idx, 'contributor_name'].fillna('').astype(str)
    # Only LAST, CREDENTIAL, FIRST.
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

        credential_part = match.group(2).strip().rstrip('.').strip()

        if not _CREDENTIAL_RE.match(credential_part):
            continue

        first_clean = match.group(3).strip().rstrip('.').strip()
        last_clean = match.group(1).strip().rstrip('.').strip()
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

    mask = (
        (first_names.str.upper() == last_names.str.upper())
        & (first_names != '')
        & first_names.str.contains(' ', regex=False)
    )
    hits = indiv_idx[mask]
    n_fixed = len(hits)

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
