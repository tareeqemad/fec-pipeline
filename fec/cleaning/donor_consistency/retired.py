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


def _fill_prev_employer_from_donor(df: pd.DataFrame) -> int:
    """Fill a retiree's prior employer from an earlier filing."""
    if 'previous_employer' not in df.columns:
        return 0

    need_fill = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_employer'] == 'RETIRED')
        & (_norm(df['previous_employer']) == '')
    )
    if not need_fill.any():
        return 0

    dates = pd.to_datetime(df['contribution_receipt_date'], errors='coerce')
    target_donors = set(df.loc[need_fill, 'donor_key'])
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
        return 0

    candidates['_date'] = dates.loc[candidates.index]
    by_donor = {
        donor_key: rows.sort_values('_date')
        for donor_key, rows in candidates.groupby('donor_key')
    }

    changed = 0
    for index in df.index[need_fill & dates.notna()]:
        earlier = by_donor.get(df.at[index, 'donor_key'])
        if earlier is None:
            continue
        earlier = earlier[earlier['_date'] <= dates.at[index]]
        if earlier.empty:
            continue

        latest = earlier[earlier['_date'] == earlier['_date'].max()]
        employers = latest['contributor_employer'].dropna().unique()
        if len(employers) != 1:
            continue

        df.at[index, 'previous_employer'] = employers[0]
        changed += 1
    return changed


def _retired_active_sync(df: pd.DataFrame) -> int:
    """AQ. occupation_category=RETIRED but employer_status=active -> move employer to previous_employer, unify to RETIRED; idempotent."""
    required = {'entity_type', 'occupation_category', 'employer_status',
                'contributor_employer'}
    if not required.issubset(df.columns):
        return 0

    mask = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['occupation_category'] == 'RETIRED')
        & (df['employer_status'] == 'active')
    )
    n = int(mask.sum())
    if not n:
        return 0

    # copy employer -> previous_employer where safe
    if 'previous_employer' in df.columns:
        prev_empty = _norm(df['previous_employer']).eq('')
        is_real_company = df['contributor_employer'].map(is_real_employer)
        copy_mask = mask & prev_empty & is_real_company
        if copy_mask.any():
            df.loc[copy_mask, 'previous_employer'] = df.loc[copy_mask, 'contributor_employer']

    # unify to the retired convention
    df.loc[mask, 'contributor_employer'] = 'RETIRED'
    df.loc[mask, 'employer_status'] = 'retired'

    # clear employer address fields (no current employer for retirees)
    for col in ('employer_address', 'employer_city', 'employer_state',
                'employer_zip', 'employer_latitude', 'employer_longitude'):
        if col in df.columns:
            df.loc[mask, col] = pd.NA
    if 'employer_geocode_level' in df.columns:
        df.loc[mask, 'employer_geocode_level'] = 'not_applicable'

    # audit trail
    if 'resolve_method' in df.columns:
        df.loc[mask, 'resolve_method'] = 'retired_consistency_fix'
    if 'resolve_confidence' in df.columns:
        df.loc[mask, 'resolve_confidence'] = 'NONE'

    return n


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
    if 'occupation_status' in df.columns:
        df.loc[retired, 'occupation_status'] = 'NOT_APPLICABLE'
    if 'employer_status' in df.columns:
        df.loc[retired, 'employer_status'] = 'retired'

    for column in ('employer_address', 'employer_city', 'employer_state',
                   'employer_zip', 'employer_latitude', 'employer_longitude'):
        if column in df.columns:
            df.loc[retired, column] = pd.NA
    if 'employer_geocode_level' in df.columns:
        df.loc[retired, 'employer_geocode_level'] = 'not_applicable'

    return count


def _normalize_previous_employer(df: pd.DataFrame) -> int:
    """AX. Delegate to the shared contract in fec/cleaning/previous_employer.py; idempotent."""
    return normalize_previous_employer_column(df)
