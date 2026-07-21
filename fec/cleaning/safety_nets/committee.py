"""cleaning/safety_nets/committee.py — committee / entity-type safety-net fixes."""
from __future__ import annotations

import pandas as pd
from fec.config.constants import (
    WRONG_COMMITTEE_EMPLOYERS,
)


def _fix_committee_employer(df: pd.DataFrame, is_comm: pd.Series) -> int:
    """A. Committees with missing/wrong employer → CAMPAIGN/COMMITTEE."""
    emp = df['contributor_employer'].fillna('')
    mask = is_comm & (df['contributor_employer'].isna() | emp.str.upper().str.strip().isin(WRONG_COMMITTEE_EMPLOYERS))
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = 'CAMPAIGN/COMMITTEE'
    return n


def _fix_organization_employer(df: pd.DataFrame) -> int:
    """A2. ORGANIZATION rows → employer placeholder 'ORGANIZATION'.

    Mirrors step A for committees so every non-individual follows ONE
    self-explanatory rule: the employer field of a non-individual names its
    entity class (COMMITTEE/PAC → CAMPAIGN/COMMITTEE, ORGANIZATION →
    ORGANIZATION). Before this, orgs inherited 'CAMPAIGN/COMMITTEE' from their
    pre-reclassification life — plainly wrong on a bank or a family trust —
    or sat empty, and carried employer_status='missing' as if they were people
    with unknown jobs."""
    is_org = df['entity_type'] == 'ORGANIZATION'
    mask = is_org & (df['contributor_employer'].fillna('').str.strip().str.upper() != 'ORGANIZATION')
    n = int(mask.sum())
    if n:
        df.loc[mask, 'contributor_employer'] = 'ORGANIZATION'
    return n


def _fix_individual_committee_type(df: pd.DataFrame, is_indiv: pd.Series) -> int:
    """B. Individuals: committee_type NULL → NOT_APPLICABLE."""
    mask = is_indiv & df['committee_type'].isna()
    n = int(mask.sum())
    if n:
        df.loc[mask, 'committee_type'] = 'NOT_APPLICABLE'
    return n


def _classify_committee_types(df: pd.DataFrame, is_comm: pd.Series) -> int:
    """C. Committees without committee_type → classify from name patterns."""
    comm_null = is_comm & df['committee_type'].isna()
    n = int(comm_null.sum())
    if not n:
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
    return n
