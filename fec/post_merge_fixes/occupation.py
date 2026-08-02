"""Occupation, occupation_status and occupation_category fixes that need donor_key."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.occupations import _categorize
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS

# status buckets a professional occupation must not keep (and vice versa)
_STATUS_CATS = {'SELF-EMPLOYED', 'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER', 'STUDENT'}
_STATUS_OCC = {'SELF-EMPLOYED', 'RETIRED', 'NOT EMPLOYED', 'HOMEMAKER',
               'HOUSEWIFE', 'STUDENT', 'UNEMPLOYED'}


def _rederive_occupation_status(df: pd.DataFrame) -> int:
    """AR. Re-derive occupation_status from the final occupation/employer state; rows with no occupation stay untouched."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    has_occ = _norm(df['contributor_occupation']) != ''
    has_emp = _norm(df['contributor_employer']) != ''
    st = df['occupation_status']

    # DERIVED is a provenance marker set by the donor-history fills - never overwrite it
    disclosed = is_indiv & has_occ & has_emp & ~st.isin(['DISCLOSED', 'DERIVED'])
    emp_missing = (is_indiv & has_occ & ~has_emp
                   & ~st.isin(['EMPLOYER_MISSING', 'NOT_DISCLOSED', 'DERIVED']))
    df.loc[disclosed, 'occupation_status'] = 'DISCLOSED'
    df.loc[emp_missing, 'occupation_status'] = 'EMPLOYER_MISSING'
    return int(disclosed.sum()) + int(emp_missing.sum())


def _rederive_occupation_category(df: pd.DataFrame) -> int:
    """AT. Re-derive occupation_category from the final occupation text; only stale status-bucket rows are touched."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    occ_u = occ.str.upper()
    cat = df['occupation_category'].fillna('')

    stale = (
        is_indiv & cat.isin(_STATUS_CATS)
        & (occ != '') & ~occ_u.isin(_STATUS_OCC)
    )
    idx = df.index[stale]
    n_fixed = 0
    if len(idx):
        new_cats = _categorize(df.loc[idx, 'contributor_occupation'])
        keep = new_cats.notna() & ~new_cats.isin(_STATUS_CATS) & (new_cats != 'OTHER')
        fix_idx = idx[keep.values]
        if len(fix_idx):
            df.loc[fix_idx, 'occupation_category'] = new_cats[keep].values
        n_fixed = int(len(fix_idx))

    # inverse direction: a status-word occupation must not keep a professional category
    torn = is_indiv & occ_u.isin(_STATUS_OCC) & (cat != '') & ~cat.isin(_STATUS_CATS)
    if torn.any():
        status_cat = occ_u[torn].replace({'HOUSEWIFE': 'HOMEMAKER', 'UNEMPLOYED': 'NOT EMPLOYED'})
        df.loc[torn, 'occupation_category'] = status_cat.values
        n_fixed += int(torn.sum())

    # NOT DISCLOSED is deliberately uncategorized (see OCCUPATION_FIXES)
    nd = is_indiv & (occ_u == 'NOT DISCLOSED') & (cat != '')
    if nd.any():
        df.loc[nd, 'occupation_category'] = pd.NA
        n_fixed += int(nd.sum())

    return n_fixed


def _occupation_consolidation(df: pd.DataFrame) -> int:
    """AE. Same donor + same employer -> most common occupation when >=3x dominant and substring-related."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    n_fixed = 0

    for (dk, emp), grp in indiv.groupby(['donor_key', 'contributor_employer'], dropna=False):
        if pd.isna(emp) or emp in SKIP_EMPLOYERS:
            continue
        occs = grp['contributor_occupation'].dropna().unique()
        if len(occs) < 2:
            continue

        counts = grp['contributor_occupation'].value_counts()
        canonical = counts.index[0]
        canonical_count = counts.iloc[0]

        for other_occ in occs:
            if other_occ == canonical:
                continue
            other_count = counts[other_occ]
            if canonical_count >= 3 * other_count and (other_occ in canonical or canonical in other_occ):
                mask = (
                    (df['donor_key'] == dk)
                    & (df['contributor_employer'] == emp)
                    & (df['contributor_occupation'] == other_occ)
                )
                df.loc[mask, 'contributor_occupation'] = canonical
                n_fixed += int(mask.sum())

    return n_fixed


def _fill_occupation_from_donor(df: pd.DataFrame) -> int:
    """AJ. Fill NaN occupation from same donor's other records (needs donor_key)."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    null_occ = indiv[indiv['contributor_occupation'].isna()]
    if null_occ.empty:
        return 0

    n_fixed = 0
    for dk, grp in null_occ.groupby('donor_key'):
        all_recs = df[(df['donor_key'] == dk) & (df['entity_type'] == 'INDIVIDUAL')]
        real_recs = all_recs[all_recs['contributor_occupation'].notna()
                             & ~all_recs['contributor_occupation'].isin(SKIP_OCCUPATIONS)
                             & (all_recs['contributor_occupation'] != '')]
        if real_recs.empty:
            continue

        # latest-dated qualifying filing - closest in time = best estimate
        main_occ = real_recs.sort_values(
            'contribution_receipt_date', na_position='first'
        )['contributor_occupation'].iloc[-1]
        main_cat = all_recs[all_recs['contributor_occupation'] == main_occ]['occupation_category'].dropna()
        main_cat = main_cat.value_counts().index[0] if len(main_cat) > 0 else None

        mask = (df['donor_key'] == dk) & df['contributor_occupation'].isna()
        df.loc[mask, 'contributor_occupation'] = main_occ
        df.loc[mask, 'occupation_status'] = 'DERIVED'   # filled from donor history
        if main_cat:
            df.loc[mask, 'occupation_category'] = main_cat
        n_fixed += int(mask.sum())

    return n_fixed


def _not_applicable_individual_sweep(df: pd.DataFrame) -> int:
    """AM. Individuals left with occupation_status='NOT_APPLICABLE' (committee-only value) get it re-derived; final net after donor_match."""
    bad = (df['entity_type'] == 'INDIVIDUAL') & (df['occupation_status'] == 'NOT_APPLICABLE')
    n = int(bad.sum())
    if not n:
        return 0

    occ = df.loc[bad, 'contributor_occupation'].astype('string').str.strip().str.upper()
    is_empty = occ.isna() | occ.eq('')
    is_notdisc = occ.eq('NOT DISCLOSED')

    df.loc[bad & is_empty.reindex(df.index, fill_value=False),   'occupation_status'] = 'MISSING'
    df.loc[bad & is_notdisc.reindex(df.index, fill_value=False), 'occupation_status'] = 'NOT_DISCLOSED'
    remaining = bad & ~(is_empty | is_notdisc).reindex(df.index, fill_value=False)
    df.loc[remaining, 'occupation_status'] = 'DISCLOSED'
    return n
