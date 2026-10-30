"""Committee / entity-type safety-net fixes."""
from __future__ import annotations

import numpy as np
import pandas as pd

from fec.cleaning.work.status import is_real_employer

_ORG_SURNAMES = frozenset({
    'FOUNDATION', 'FUND', 'TRUST', 'ASSOCIATION', 'SOCIETY', 'INSTITUTE',
})
_TITLES = frozenset({
    'MR', 'MRS', 'MS', 'DR', 'MD', 'ESQ', 'JR', 'SR',
    'II', 'III', 'IV', 'PHD', 'DDS', 'DO', 'RN', 'CPA',
})


# clear employer field for committee/PAC rows
def _fix_committee_employer(df: pd.DataFrame, is_comm: pd.Series) -> int:
    """A. Committees carry no employer -> clear it."""
    mask = is_comm & (df['contributor_employer'].fillna('').str.strip() != '')
    n_fixed = int(mask.sum())
    if n_fixed:
        df.loc[mask, 'contributor_employer'] = np.nan
    return n_fixed


# reclassify an organization name parsed as a person
def _fix_misclassified_foundation(df: pd.DataFrame) -> int:
    """Reclassify an organization parsed as a person."""
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

    employer = df.loc[mask, 'contributor_employer']
    has_employer = mask & employer.notna() & (employer.str.len() > 3)
    has_employer &= employer.map(is_real_employer)
    df.loc[has_employer, 'contributor_name'] = employer[has_employer].str.upper()
    return changed

