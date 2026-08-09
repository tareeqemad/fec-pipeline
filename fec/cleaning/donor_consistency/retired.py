"""Retired-status fixes and the previous_employer column they feed, all needing donor_key."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.previous_employer import normalize_previous_employer_column
from fec.config.constants import (
    EMPLOYER_STATUS_VALUES,
    NON_RETIRED_EMPLOYER_STATUSES,
    NOT_EMPLOYED_VARIANTS,
    RETIRED_PREVIOUS_EMPLOYER_PLACEHOLDERS,
    SELF_EMPLOYED_VARIANTS,
    SKIP_EMPLOYERS,
)

def _retired_while_active(df: pd.DataFrame) -> int:
    """AA. RETIRED entries while donor is still active -> restore the real employer."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emp_vals = set(grp['contributor_employer'].dropna().unique())
        if 'RETIRED' not in emp_vals:
            continue
        real_emps = emp_vals - SKIP_EMPLOYERS
        if not real_emps:
            continue

        ret_rows = grp[grp['contributor_employer'] == 'RETIRED']
        active_rows = grp[grp['contributor_employer'].isin(real_emps)]

        # not(<=) rather than '>': NaT dates compare False either way, so only
        # this form keeps skipping donors whose dates are unparseable
        if not (ret_rows['contribution_receipt_date'].max() <= active_rows['contribution_receipt_date'].max()):
            continue
        main_emp = active_rows['contributor_employer'].value_counts().index[0]
        sub = active_rows[active_rows['contributor_employer'] == main_emp]
        occ_vc = sub['contributor_occupation'].value_counts()
        if occ_vc.empty:
            continue          # no occupation to propagate - skip this donor
        main_occ = occ_vc.index[0]
        main_cat = sub['occupation_category'].mode()
        main_cat = main_cat.iloc[0] if len(main_cat) > 0 else 'OTHER'

        mask = (df['donor_key'] == dk) & (df['contributor_employer'] == 'RETIRED')
        df.loc[mask, 'contributor_employer'] = main_emp
        df.loc[mask, 'contributor_occupation'] = main_occ
        df.loc[mask, 'occupation_category'] = main_cat
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'
        n_fixed += int(mask.sum())
    return n_fixed


def _selfemployed_while_retired(df: pd.DataFrame) -> int:
    """AF. Stray SELF-EMPLOYED rows -> RETIRED when the donor is clearly retired."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].value_counts()
        if 'RETIRED' not in emps or 'SELF-EMPLOYED' not in emps:
            continue

        ret_count = emps['RETIRED']
        se_count = emps['SELF-EMPLOYED']
        total = len(grp)

        # pd.isna first: NA != 'RETIRED' evaluates to NA, which raises inside `if`
        latest_emp = grp.sort_values('contribution_receipt_date', ascending=False).iloc[0]['contributor_employer']
        if pd.isna(latest_emp) or latest_emp != 'RETIRED':
            continue

        if ret_count / total >= 0.6 and se_count <= 3:
            mask = (df['donor_key'] == dk) & (df['contributor_employer'] == 'SELF-EMPLOYED')
            df.loc[mask, 'contributor_employer'] = 'RETIRED'
            df.loc[mask, 'contributor_occupation'] = 'RETIRED'
            df.loc[mask, 'occupation_category'] = 'RETIRED'
            n_fixed += int(mask.sum())

    return n_fixed


def _swapped_emp_occ_retired(df: pd.DataFrame) -> int:
    """AH. Mostly-retired donors: stray real-employer rows with occ=RETIRED are a form swap -> RETIRED."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].value_counts()
        ret_count = emps.get('RETIRED', 0)
        if ret_count == 0:
            continue

        real_emps = set(emps.index) - SKIP_EMPLOYERS
        if not real_emps:
            continue

        real_count = sum(emps[e] for e in real_emps)

        # ratio: RETIRED vs (RETIRED + real employers) - ignores NONE/NOT EMPLOYED/etc.
        if ret_count / (ret_count + real_count) < 0.70:
            continue

        for emp in real_emps:
            emp_count = emps.get(emp, 0)
            if emp_count > 3:
                continue  # too many to be a stray

            swap_mask = (
                (df['donor_key'] == dk)
                & (df['contributor_employer'] == emp)
                & (df['contributor_occupation'].fillna('').str.upper() == 'RETIRED')
            )
            n = int(swap_mask.sum())
            if n > 0 and n == emp_count:
                # all records with this employer have occ=RETIRED -> confirmed swap
                df.loc[swap_mask, 'contributor_employer'] = 'RETIRED'
                df.loc[swap_mask, 'contributor_occupation'] = 'RETIRED'
                df.loc[swap_mask, 'occupation_category'] = 'RETIRED'
                n_fixed += n

    return n_fixed


def _once_retired_always_retired(df: pd.DataFrame) -> int:
    """AO. Collapse NOT EMPLOYED / SELF-EMPLOYED to RETIRED for donors who never listed a real employer."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'

    emp_upper = _norm(df['contributor_employer'])

    is_retired  = emp_upper.eq('RETIRED')
    is_notemp   = emp_upper.isin(NOT_EMPLOYED_VARIANTS)
    is_selfemp  = emp_upper.isin(SELF_EMPLOYED_VARIANTS)
    # "real" = not a status word; '' (in EMPLOYER_STATUS_VALUES) covers NaN too
    is_real = ~emp_upper.isin(EMPLOYER_STATUS_VALUES)

    donor_has = pd.DataFrame({
        'donor_key': df.loc[is_indiv, 'donor_key'],
        'retired':  is_retired[is_indiv],
        'notemp':   is_notemp[is_indiv],
        'selfemp':  is_selfemp[is_indiv],
        'real':     is_real[is_indiv],
    }).groupby('donor_key').agg('any')

    target_donors = donor_has.index[
        donor_has['retired']
        & ~donor_has['real']
        & (donor_has['notemp'] | donor_has['selfemp'])
    ]
    if len(target_donors) == 0:
        return 0

    to_change = is_indiv & df['donor_key'].isin(target_donors) & (is_notemp | is_selfemp)
    n = int(to_change.sum())

    df.loc[to_change, 'contributor_employer'] = 'RETIRED'

    # only update occupation / category if they were status words themselves
    occ_upper = _norm(df.loc[to_change, 'contributor_occupation'])
    occ_is_status = occ_upper.isin(NON_RETIRED_EMPLOYER_STATUSES)
    occ_idx = df.loc[to_change].index[occ_is_status.values]
    df.loc[occ_idx, 'contributor_occupation'] = 'RETIRED'
    df.loc[occ_idx, 'occupation_category']    = 'RETIRED'

    if 'employer_status' in df.columns:
        df.loc[to_change, 'employer_status'] = 'retired'

    return n


def _fill_prev_employer_from_donor(df: pd.DataFrame) -> int:
    """AN. Fill retired donors' previous_employer from their other records (same donor_key only)."""
    if 'previous_employer' not in df.columns:
        return 0

    need_fill = (
        (df['entity_type'] == 'INDIVIDUAL')
        & (df['contributor_employer'] == 'RETIRED')
        & (_norm(df['previous_employer']) == '')
    )
    if not need_fill.any():
        return 0

    target_dks = set(df.loc[need_fill, 'donor_key'].unique())

    # candidate pool: other individual rows with a real (non-status) employer
    candidates = df[
        (df['donor_key'].isin(target_dks))
        & ~need_fill
        & (df['entity_type'] == 'INDIVIDUAL')
        & df['contributor_employer'].notna()
    ].copy()
    candidates = candidates[
        ~_norm(candidates['contributor_employer']).isin(EMPLOYER_STATUS_VALUES)
    ]
    if candidates.empty:
        return 0

    # per donor: most common real employer, ties broken by recency
    candidates = candidates.sort_values('contribution_receipt_date', ascending=False)

    best: dict[str, str] = {}
    for dk, grp in candidates.groupby('donor_key'):
        vc = grp['contributor_employer'].value_counts()
        best[dk] = vc.index[0]

    fillable = need_fill & df['donor_key'].isin(best)
    n = int(fillable.sum())
    if n:
        df.loc[fillable, 'previous_employer'] = df.loc[fillable, 'donor_key'].map(best)
    return n


def _propagate_previous_employer_within_donor(df: pd.DataFrame) -> int:
    """AP. Copy a donor's known previous_employer to their empty RETIRED rows; runs after AO collapses statuses."""
    if 'previous_employer' not in df.columns:
        return 0
    is_retired = (df['entity_type'] == 'INDIVIDUAL') & (df['contributor_employer'] == 'RETIRED')
    if not is_retired.any():
        return 0

    empty = _norm(df.loc[is_retired, 'previous_employer']).eq('')
    if not empty.any():
        return 0

    # donors with at least one empty + at least one non-empty retired row
    ret_df = df.loc[is_retired, ['donor_key']].copy()
    ret_df['prev_empty'] = empty.values
    agg = ret_df.groupby('donor_key').agg(
        has_empty=('prev_empty', 'any'),
        has_value=('prev_empty', lambda x: (~x).any()),
    )
    candidate_dks = agg.index[agg['has_empty'] & agg['has_value']]
    if len(candidate_dks) == 0:
        return 0

    # per donor: most common non-empty value, ties broken by recency via the sort
    ret_sorted = df.loc[is_retired].sort_values('contribution_receipt_date', ascending=False)
    ret_sorted = ret_sorted[ret_sorted['donor_key'].isin(candidate_dks)]
    ret_sorted = ret_sorted[_norm(ret_sorted['previous_employer']) != '']

    best: dict[str, str] = {}
    for dk, grp in ret_sorted.groupby('donor_key'):
        vc = grp['previous_employer'].value_counts()
        best[dk] = vc.index[0]

    fill_mask = (
        is_retired
        & df['donor_key'].isin(best)
        & empty.reindex(df.index, fill_value=False)
    )
    n = int(fill_mask.sum())
    if n:
        df.loc[fill_mask, 'previous_employer'] = df.loc[fill_mask, 'donor_key'].map(best)
    return n


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
        is_real_company = ~_norm(df['contributor_employer']).isin(
            RETIRED_PREVIOUS_EMPLOYER_PLACEHOLDERS
        )
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
    is_real_company = ~employer.isin(
        EMPLOYER_STATUS_VALUES | RETIRED_PREVIOUS_EMPLOYER_PLACEHOLDERS
    )
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
