"""Use donor history for retired filings."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.work.status import is_real_employer
from fec.config.constants import (
    STATUS_CATEGORIES,
    STATUS_WORDS,
)


# Find each row's latest employer filed by that donor on or before its date.
# Skip ties between employers and rows with no earlier working filing.
def dated_previous_employers(df: pd.DataFrame, rows: pd.Series) -> dict:
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
        donor_key: group
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


# Fill a retiree's prior employer from an earlier filing.
def _fill_prev_employer_from_donor(df: pd.DataFrame) -> int:
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


# Move recovered companies to previous_employer when the filing still says RETIRED.
def _settle_retired_employer(df: pd.DataFrame) -> int:
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
