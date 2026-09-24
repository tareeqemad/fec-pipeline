"""Retired-status fixes and the previous_employer column they feed, all needing donor_key."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.previous_employer import (
    is_real_employer,
    normalize_previous_employer_column,
)
from fec.config.constants import (
    STATUS_CATEGORIES,
    STATUS_WORDS,
)


def dated_previous_employers(df: pd.DataFrame, rows: pd.Series) -> dict:
    """{row index: the donor's own latest real employer filed on or before that row's date}.

    A tie of two employers on the latest date names none; rows with no earlier
    working filing are left out.
    """
    dates = pd.to_datetime(df['contribution_receipt_date'], errors='coerce')
    rows = rows & dates.notna()
    if not rows.any():
        return {}
    target_donors = set(df.loc[rows, 'donor_key'])
    blank = pd.Series('', index=df.index)
    occupation = _norm(df.get('contributor_occupation', blank))
    category = _norm(df.get('occupation_category', blank))
    not_working = (
        occupation.isin(STATUS_WORDS - {'SELF-EMPLOYED'})
        | category.isin(STATUS_CATEGORIES - {'SELF-EMPLOYED'})
    )
    candidates = df[
        df['entity_type'].eq('INDIVIDUAL')
        & df['donor_key'].isin(target_donors)
        & df['contributor_employer'].map(is_real_employer)
        & ~not_working
        & dates.notna()
    ].copy()
    if candidates.empty:
        return {}

    candidates['_date'] = dates.loc[candidates.index]
    by_donor = {
        donor_key: group.sort_values('_date')
        for donor_key, group in candidates.groupby('donor_key')
    }

    found = {}
    for index in df.index[rows]:
        earlier = by_donor.get(df.at[index, 'donor_key'])
        if earlier is None:
            continue
        earlier = earlier[earlier['_date'] <= dates.at[index]]
        if earlier.empty:
            continue
        latest = earlier[earlier['_date'] == earlier['_date'].max()]
        employers = latest['contributor_employer'].dropna().unique()
        if len(employers) == 1:
            found[index] = employers[0]
    return found


def _fill_prev_employer_from_donor(df: pd.DataFrame) -> int:
    """Fill a retiree's prior employer from an earlier filing."""
    if 'previous_employer' not in df.columns:
        return 0

    need_fill = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_employer'] == 'RETIRED')
        & (_norm(df['previous_employer']) == '')
    )
    found = dated_previous_employers(df, need_fill)
    for index, employer in found.items():
        df.at[index, 'previous_employer'] = employer
    return len(found)


def _settle_retired_employer(df: pd.DataFrame) -> int:
    """Move a final retired row's recovered company to previous_employer.

    Employer recovery runs late and can correctly recover a real company while
    the filing still explicitly says RETIRED. Preserve that company as prior
    work, then restore the retired current-employer convention.
    """
    required = {
        'entity_type', 'contributor_employer', 'contributor_occupation',
        'occupation_category',
    }
    if not required.issubset(df.columns):
        return 0

    employer = _norm(df['contributor_employer'])
    retired = (
        df['entity_type'].eq('INDIVIDUAL')
        & df['occupation_category'].eq('RETIRED')
        & _norm(df['contributor_occupation']).eq('RETIRED')
        & employer.ne('RETIRED')
    )
    count = int(retired.sum())
    if not count:
        return 0

    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.NA
    previous_empty = _norm(df['previous_employer']).eq('')
    is_real_company = df['contributor_employer'].map(is_real_employer)
    copy = retired & previous_empty & is_real_company
    df.loc[copy, 'previous_employer'] = df.loc[copy, 'contributor_employer']

    df.loc[retired, 'contributor_employer'] = 'RETIRED'

    return count


def _normalize_previous_employer(df: pd.DataFrame) -> int:
    """AX. Delegate to the shared contract in fec/cleaning/previous_employer.py; idempotent."""
    return normalize_previous_employer_column(df)
