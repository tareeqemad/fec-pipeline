"""Entity-type overrides, consistency and employer clearing."""
import csv

import numpy as np
import pandas as pd

from fec.cleaning.entity_source import deciding_source_type
from fec.cleaning.pipeline.reclassify import enforce_entity_name_consistency
from fec.env import RULES_DIR


# clear employer field for committees/organizations after re-typing
def clear_nonindividual_employer_field(df: pd.DataFrame) -> int:
    mask = (df['entity_type'].isin(('COMMITTEE/PAC', 'ORGANIZATION'))
            & (df['contributor_employer'].fillna('').str.strip() != ''))
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n


# apply hand-curated entity_type corrections from overrides csv
def apply_entity_overrides(df: pd.DataFrame) -> int:
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


# read curated name to entity_type overrides
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


# re-apply FEC's entity type after later retyping
def apply_source_entity_types(df: pd.DataFrame) -> int:
    source = deciding_source_type(df)
    n = 0
    for et in sorted(set(source) - {''}):
        mask = source.eq(et) & df['entity_type'].ne(et)
        if mask.any():
            _set_entity_type(df, mask, et)
            n += int(mask.sum())
    return n


# set entity_type and the fields it implies
def _set_entity_type(df: pd.DataFrame, mask: pd.Series, et: str) -> None:
    df.loc[mask, 'entity_type'] = et
    if et == 'ORGANIZATION':
        df.loc[mask, 'is_individual'] = False
        df.loc[mask, 'occupation_category'] = 'ORGANIZATION'
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'contributor_employer'] = np.nan
    elif et == 'COMMITTEE/PAC':
        # mirror a real committee row
        df.loc[mask, 'is_individual'] = False
        df.loc[mask, 'contributor_employer'] = np.nan
        df.loc[mask, 'contributor_occupation'] = np.nan
        df.loc[mask, 'occupation_category'] = 'POLITICAL COMMITTEE'
    elif et == 'INDIVIDUAL':
        # real person mistyped; rebuild name parts
        df.loc[mask, 'is_individual'] = True
        parts = df.loc[mask, 'contributor_name'].fillna('').str.split(',', n=1)
        df.loc[mask, 'contributor_last_name'] = parts.str[0].str.strip()
        df.loc[mask, 'contributor_first_name'] = (
            parts.str[1].fillna('').str.strip().str.lstrip('. ')
        )
        df.loc[mask, 'occupation_category'] = 'OTHER'


# same name, same entity_type; after name canonicalization
def reenforce_entity_consistency(df: pd.DataFrame) -> int:
    before = df['entity_type'].copy()
    n = enforce_entity_name_consistency(df)
    if n:
        flipped = (before == 'COMMITTEE/PAC') & (df['entity_type'] == 'ORGANIZATION')
        df.loc[flipped, 'occupation_category'] = 'ORGANIZATION'
    return n
