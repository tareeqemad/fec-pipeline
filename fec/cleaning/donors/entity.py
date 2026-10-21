"""Entity-type fixes: non-individual employer clearing, hand-curated overrides, name consistency."""
import csv

import numpy as np
import pandas as pd

from fec.cleaning.entities import deciding_source_type
from fec.cleaning.entities import _enforce_entity_name_consistency
from fec.env import RULES_DIR


# clear employer field for committees/organizations after re-typing
def _clear_nonindividual_employer_field(df: pd.DataFrame) -> int:
    """AW. Non-individuals carry no employer -> clear it; must run after AU/AV re-type rows."""
    mask = (df['entity_type'].isin(('COMMITTEE/PAC', 'ORGANIZATION'))
            & (df['contributor_employer'].fillna('').str.strip() != ''))
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


# Apply documented entity corrections by name after restoring FEC types.
def _apply_entity_overrides(df: pd.DataFrame) -> int:
    overrides = read_entity_overrides()
    if not overrides:
        return 0

    name_u = df['contributor_name'].fillna('').str.upper().str.strip()
    n = 0
    for nm_u, et in overrides.items():
        mask = name_u == nm_u
        if mask.any():
            _set_entity_type(df, mask, et)
            n += int(mask.sum())
    return n


# contributor_name -> entity_type from the curated overrides file
def read_entity_overrides() -> dict[str, str]:
    path = RULES_DIR / 'entity_overrides.csv'
    if not path.exists():
        return {}
    overrides: dict[str, str] = {}
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            nm = (row.get('contributor_name') or '').strip().upper()
            et = (row.get('entity_type') or '').strip().upper()
            if nm and et:
                overrides[nm] = et
    return overrides


# re-apply FEC's own entity type where a later step changed it
def _apply_source_entity_types(df: pd.DataFrame) -> int:
    """A safety net or name rule may retype a row after classification; the
    type FEC gave wins again here, before the documented overrides."""
    source = deciding_source_type(df)
    n = 0
    for et in sorted(set(source) - {''}):
        mask = source.eq(et) & df['entity_type'].ne(et)
        if mask.any():
            _set_entity_type(df, mask, et)
            n += int(mask.sum())
    return n


# set rows' entity_type and the fields that type carries
def _set_entity_type(df: pd.DataFrame, mask: pd.Series, et: str) -> None:
    df.loc[mask, 'entity_type'] = et
    if et in ('ORGANIZATION', 'COMMITTEE/PAC'):
        df.loc[mask, 'is_individual'] = False
        df.loc[mask, 'occupation_category'] = (
            'ORGANIZATION' if et == 'ORGANIZATION' else 'POLITICAL COMMITTEE'
        )
        # Names clear after consistency.
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'contributor_employer'] = np.nan
    elif et == 'INDIVIDUAL':
        # a real person mistyped as a committee - parse 'LAST, FIRST' back out
        df.loc[mask, 'is_individual'] = True
        parts = df.loc[mask, 'contributor_name'].fillna('').str.split(',', n=1)
        df.loc[mask, 'contributor_last_name'] = parts.str[0].str.strip()
        df.loc[mask, 'contributor_first_name'] = (
            parts.str[1].fillna('').str.strip().str.lstrip('. ')
        )
        df.loc[mask, 'occupation_category'] = 'OTHER'


# re-enforce same-name donors share one entity_type
def _reenforce_entity_consistency(df: pd.DataFrame) -> int:
    """AU. Re-enforce same-name -> same entity_type; must run after canonicalize_donor_names has unified names."""
    before = df['entity_type'].copy()
    n = _enforce_entity_name_consistency(df)
    if n:
        flipped = (before == 'COMMITTEE/PAC') & (df['entity_type'] == 'ORGANIZATION')
        df.loc[flipped, 'occupation_category'] = 'ORGANIZATION'
    return n
