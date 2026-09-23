"""Occupation, occupation_status and occupation_category fixes that need donor_key."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.occupations import _categorize_final
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS


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
    """AT. Make every individual's category match their final occupation."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    expected = _categorize_final(df.loc[is_indiv, 'contributor_occupation'])
    current = df.loc[is_indiv, 'occupation_category'].fillna('')
    changed = current.ne(expected)

    df.loc[expected.index[changed], 'occupation_category'] = expected.loc[changed]
    return int(changed.sum())


def _one_confirmed_role(df, donor_key, employer):
    """Return the donor's only real role at this employer, or None."""
    same_job = (
        df['donor_key'].eq(donor_key)
        & df['entity_type'].eq('INDIVIDUAL')
        & df['contributor_employer'].eq(employer)
    )
    occupations = df.loc[same_job, 'contributor_occupation'].dropna()
    occupations = occupations[
        occupations.ne('') & ~occupations.isin(SKIP_OCCUPATIONS)
    ]

    employer_key = canonical_key(employer)
    occupations = occupations[
        occupations.map(canonical_key).ne(employer_key)
    ].unique()
    return occupations[0] if len(occupations) == 1 else None


def _set_derived_occupation(df, rows, occupation):
    df.loc[rows, 'contributor_occupation'] = occupation
    df.loc[rows, 'occupation_category'] = _categorize_final(
        pd.Series(occupation, index=rows)
    )
    df.loc[rows, 'occupation_status'] = 'DERIVED'


def _fill_occupation_from_donor(df: pd.DataFrame) -> int:
    """AJ. Recover a missing occupation from the same donor and employer.

    Employer identity is already settled by the previous step, so an inferred
    employer is safe here. The same-employer guard prevents cross-job guesses.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    employer = indiv['contributor_employer']
    missing_occ = indiv[
        indiv['contributor_occupation'].isna()
        & employer.notna()
        & ~employer.isin(SKIP_EMPLOYERS)
    ]

    n_fixed = 0
    for (dk, emp), missing in missing_occ.groupby(['donor_key', 'contributor_employer']):
        same_job = (
            (df['donor_key'] == dk)
            & (df['entity_type'] == 'INDIVIDUAL')
            & (df['contributor_employer'] == emp)
        )
        real_recs = df[
            same_job
            & df['contributor_occupation'].notna()
            & ~df['contributor_occupation'].isin(SKIP_OCCUPATIONS)
            & df['contributor_occupation'].ne('')
        ]
        if real_recs.empty:
            continue

        counts = real_recs['contributor_occupation'].value_counts()
        if len(counts) > 1 and counts.iloc[0] == counts.iloc[1]:
            continue

        main_occ = counts.index[0]
        df.loc[missing.index, 'contributor_occupation'] = main_occ
        df.loc[missing.index, 'occupation_category'] = _categorize_final(
            pd.Series(main_occ, index=missing.index)
        )
        df.loc[missing.index, 'occupation_status'] = 'DERIVED'
        n_fixed += len(missing)

    # SELF-EMPLOYED is sometimes filed as the occupation beside a named company.
    # Recover it only when this donor has one clear role at that same company.
    placeholder_occ = indiv[
        indiv['contributor_occupation'].eq('SELF-EMPLOYED')
        & employer.notna()
        & ~employer.isin(SKIP_EMPLOYERS)
    ]
    for (dk, emp), placeholders in placeholder_occ.groupby(
        ['donor_key', 'contributor_employer']
    ):
        occupation = _one_confirmed_role(df, dk, emp)
        if occupation is None:
            continue

        _set_derived_occupation(df, placeholders.index, occupation)
        n_fixed += len(placeholders)

    # A company occasionally lands in both fields (for example KIRKLAND &
    # ELLIS beside KIRKLAND & ELLIS LLP). Legal suffixes and punctuation are
    # ignored only for detecting this placeholder; the role still needs unique
    # same-donor, same-employer evidence.
    occupation_key = indiv['contributor_occupation'].fillna('').map(canonical_key)
    employer_key = employer.fillna('').map(canonical_key)
    company_occ = indiv[
        occupation_key.ne('')
        & occupation_key.eq(employer_key)
        & employer.notna()
        & ~employer.isin(SKIP_EMPLOYERS)
    ]
    for (dk, emp), placeholders in company_occ.groupby(
        ['donor_key', 'contributor_employer']
    ):
        occupation = _one_confirmed_role(df, dk, emp)
        if occupation is None:
            continue

        _set_derived_occupation(df, placeholders.index, occupation)
        n_fixed += len(placeholders)

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


def _converge_occupation_within_employer(df: pd.DataFrame) -> int:
    """AU. One job, one spelling: a donor's occupations at ONE employer that fall in the same
    category and were filed over OVERLAPPING periods (PHYSICIAN in 2022-2024 next to CARDIOLOGIST
    in 2023-2026, ATTORNEY next to LAWYER) are the same job written two ways, so every row takes
    the spelling filed most often (ties: the most recent). A different category (CEO next to
    PHYSICIAN) or non-overlapping periods (ASSOCIATE then PARTNER) is a real change and is kept."""
    is_job = (
        df['entity_type'].eq('INDIVIDUAL')
        & _norm(df['contributor_employer']).ne('')
        & ~df['contributor_employer'].isin(SKIP_EMPLOYERS)
        & _norm(df['contributor_occupation']).ne('')
        & ~df['contributor_occupation'].isin(SKIP_OCCUPATIONS)
    )
    jobs = df.loc[is_job, ['donor_key', 'contributor_employer', 'contributor_occupation',
                           'occupation_category', 'contribution_receipt_date']].copy()
    jobs['date'] = pd.to_datetime(jobs['contribution_receipt_date'], errors='coerce')
    multi = jobs.groupby(['donor_key', 'contributor_employer'])['contributor_occupation'].transform('nunique') > 1
    jobs = jobs[multi]
    if jobs.empty:
        return 0

    n_fixed = 0
    for (donor_key, employer), group in jobs.groupby(['donor_key', 'contributor_employer']):
        if group['occupation_category'].fillna('').nunique() != 1:
            continue
        spans = group.groupby('contributor_occupation')['date'].agg(['min', 'max', 'size']).sort_values('min')
        if spans['min'].isna().any():
            continue
        # every spelling must overlap the one before it in time; a gap means a real change of job
        if any(spans['min'].iloc[i + 1] > spans['max'].iloc[i] for i in range(len(spans) - 1)):
            continue
        dominant = spans.sort_values(['size', 'max'], ascending=[False, False]).index[0]
        rows = group.index[group['contributor_occupation'] != dominant]
        if len(rows):
            df.loc[rows, 'contributor_occupation'] = dominant
            n_fixed += len(rows)
    return n_fixed
