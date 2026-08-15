"""Committee / entity-type safety-net fixes."""
from __future__ import annotations

import numpy as np
import pandas as pd

_ORG_SURNAMES = frozenset({
    'FOUNDATION', 'FUND', 'TRUST', 'ASSOCIATION', 'SOCIETY', 'INSTITUTE',
})
_TITLES = frozenset({
    'MR', 'MRS', 'MS', 'DR', 'MD', 'ESQ', 'JR', 'SR',
    'II', 'III', 'IV', 'PHD', 'DDS', 'DO', 'RN', 'CPA',
})


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


def _fix_misclassified_foundation(df: pd.DataFrame) -> int:
    """Reclassify an organization parsed as a person."""
    from fec.cleaning.previous_employer import is_real_employer

    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_last_name'].fillna('').str.upper().isin(_ORG_SURNAMES)
        & first.str.split().str.len().ge(2)
    )
    changed = int(mask.sum())
    if not changed:
        return 0

    df.loc[mask, 'entity_type'] = 'COMMITTEE/PAC'
    df.loc[mask, 'is_individual'] = False
    df.loc[mask, 'contributor_first_name'] = np.nan
    df.loc[mask, 'contributor_last_name'] = np.nan
    df.loc[mask, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[mask, 'committee_type'] = 'ORGANIZATION'

    employer = df.loc[mask, 'contributor_employer']
    has_employer = mask & employer.notna() & (employer.str.len() > 3)
    has_employer &= employer.map(is_real_employer)
    df.loc[has_employer, 'contributor_name'] = employer[has_employer].str.upper()
    return changed


def _fix_title_as_first_name(df: pd.DataFrame) -> int:
    """Clear titles parsed as first names."""
    is_individual = df['entity_type'] == 'INDIVIDUAL'
    first_names = df['contributor_first_name'].fillna('')
    empty_first = is_individual & first_names.eq('')
    names = df.loc[empty_first, 'contributor_name'].fillna('')
    after_comma = names.str.split(',', n=1).str[1].str.strip()
    title_after_comma = empty_first & after_comma.reindex(
        df.index, fill_value=''
    ).isin(_TITLES)
    mask = title_after_comma | (is_individual & first_names.isin(_TITLES))
    changed = int(mask.sum())
    if changed:
        df.loc[mask, 'contributor_first_name'] = np.nan
    return changed
