"""Committee / entity-type safety-net fixes."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _fix_committee_employer(df: pd.DataFrame, is_comm: pd.Series) -> int:
    """A. Committees carry no employer -> clear it."""
    mask = is_comm & (df['contributor_employer'].fillna('').str.strip() != '')
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n_fixed


def _fix_individual_committee_type(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """B. Individuals: committee_type NULL -> NOT_APPLICABLE."""
    mask = is_indiv & df['committee_type'].isna()
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'committee_type'] = 'NOT_APPLICABLE'
    return n_fixed


def _classify_committee_types(df: pd.DataFrame, is_comm: pd.Series) -> int:
    """C. Committees without committee_type: classify from name patterns."""
    comm_null = is_comm & df['committee_type'].isna()
    n_fixed = int(comm_null.sum())
    if not n_fixed:
        return 0

    names = df.loc[comm_null, 'contributor_name'].fillna('').str.upper()
    is_pac = names.str.contains(r'\bPAC\b|POLITICAL ACTION', na=False)
    df.loc[comm_null & is_pac, 'committee_type'] = 'POLITICAL ACTION COMMITTEE'

    is_trust = names.str.contains(r'\bTRUST\b|\bFUND\b', na=False) & ~is_pac
    df.loc[comm_null & is_trust, 'committee_type'] = 'ORGANIZATION'

    is_org = comm_null & (df['occupation_category'] == 'ORGANIZATION')
    df.loc[is_org & df['committee_type'].isna(), 'committee_type'] = 'ORGANIZATION'

    still_null = comm_null & df['committee_type'].isna()
    df.loc[still_null, 'committee_type'] = 'POLITICAL COMMITTEE'
    return n_fixed
