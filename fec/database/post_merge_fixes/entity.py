"""Entity-type fixes: non-individual employer clearing, hand-curated overrides, name consistency."""
import csv

import numpy as np
import pandas as pd

from fec.env import PROJECT_ROOT


def _clear_nonindividual_employer_field(df: pd.DataFrame) -> int:
    """AW. Non-individuals carry no employer -> clear it; must run after AU/AV re-type rows."""
    mask = (df['entity_type'].isin(('COMMITTEE/PAC', 'ORGANIZATION'))
            & (df['contributor_employer'].fillna('').str.strip() != ''))
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


def _apply_entity_overrides(df: pd.DataFrame) -> int:
    """AV. Hand-curated entity_type corrections from data/database/entity_overrides.csv, keyed by contributor_name."""
    path = PROJECT_ROOT / 'data' / 'database' / 'entity_overrides.csv'
    if not path.exists():
        return 0

    overrides: dict[str, str] = {}
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            nm = (row.get('contributor_name') or '').strip().upper()
            et = (row.get('entity_type') or '').strip().upper()
            if nm and et:
                overrides[nm] = et
    if not overrides:
        return 0

    name_u = df['contributor_name'].fillna('').str.upper().str.strip()
    n = 0
    for nm_u, et in overrides.items():
        mask = name_u == nm_u
        if not mask.any():
            continue
        df.loc[mask, 'entity_type'] = et
        if et == 'ORGANIZATION':
            df.loc[mask, 'is_individual'] = False
            df.loc[mask, 'occupation_category'] = 'ORGANIZATION'
            # an org has no personal employer/occupation; first/last are cleared
            # by the non-individual name-clear step that runs after post-merge
            df.loc[mask, 'contributor_occupation'] = np.nan
            df.loc[mask, 'contributor_employer'] = np.nan
        elif et == 'COMMITTEE/PAC':
            # mirror a real committee row: no employer, POLITICAL COMMITTEE
            # category, no personal occupation
            df.loc[mask, 'is_individual'] = False
            df.loc[mask, 'contributor_employer'] = np.nan
            df.loc[mask, 'contributor_occupation'] = np.nan
            df.loc[mask, 'occupation_category'] = 'POLITICAL COMMITTEE'
        elif et == 'INDIVIDUAL':
            # a real person mistyped as a committee - parse 'LAST, FIRST' back out
            df.loc[mask, 'is_individual'] = True
            parts = df.loc[mask, 'contributor_name'].fillna('').str.split(',', n=1)
            df.loc[mask, 'contributor_last_name'] = parts.str[0].str.strip()
            df.loc[mask, 'contributor_first_name'] = (
                parts.str[1].fillna('').str.strip().str.lstrip('. ')
            )
            df.loc[mask, 'occupation_category'] = 'OTHER'
        n += int(mask.sum())
    return n


def _reenforce_entity_consistency(df: pd.DataFrame) -> int:
    """AU. Re-enforce same-name -> same entity_type; must run after canonicalize_donor_names has unified names."""
    from fec.cleaning.pipeline.reclassify import _enforce_entity_name_consistency
    before = df['entity_type'].copy()
    n = _enforce_entity_name_consistency(df)
    if n and 'occupation_category' in df.columns:
        flipped = (before == 'COMMITTEE/PAC') & (df['entity_type'] == 'ORGANIZATION')
        df.loc[flipped, 'occupation_category'] = 'ORGANIZATION'
    return n
