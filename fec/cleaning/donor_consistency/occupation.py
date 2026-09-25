"""Occupation, occupation_status and occupation_category fixes that need donor_key."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.occupations import _categorize_final
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS


# sync occupation_category to match each individual's final occupation
def _rederive_occupation_category(df: pd.DataFrame) -> int:
    """AT. Make every individual's category match their final occupation."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    expected = _categorize_final(df.loc[is_indiv, 'contributor_occupation'])
    current = df.loc[is_indiv, 'occupation_category'].fillna('')
    changed = current.ne(expected)

    df.loc[expected.index[changed], 'occupation_category'] = expected.loc[changed]
    return int(changed.sum())


# find the donor's single confirmed role at this employer
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


# write a derived occupation, category, and status
def _set_derived_occupation(df, rows, occupation):
    df.loc[rows, 'contributor_occupation'] = occupation
    df.loc[rows, 'occupation_category'] = _categorize_final(
        pd.Series(occupation, index=rows)
    )
    df.loc[rows, 'occupation_status'] = 'DERIVED'


# fill missing or placeholder occupations from the donor's other filings
def _fill_occupation_from_donor(df: pd.DataFrame) -> int:
    """AJ. Recover a missing occupation from the same donor and employer.

    Employer identity is already settled by the previous step, so an inferred
    employer is safe here. The same-employer guard prevents cross-job guesses.
    """
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    employer = indiv['contributor_employer']
    occupation = indiv['contributor_occupation']
    real_employer = employer.notna() & ~employer.isin(SKIP_EMPLOYERS)

    n_fixed = _fill_missing_occupations(df, indiv[occupation.isna() & real_employer])

    # SELF-EMPLOYED is sometimes filed as the occupation beside a named company.
    # Recover it only when this donor has one clear role at that same company.
    self_employed = indiv[occupation.eq('SELF-EMPLOYED') & real_employer]
    n_fixed += _fill_placeholder_occupations(df, self_employed)

    # A company occasionally lands in both fields (for example KIRKLAND &
    # ELLIS beside KIRKLAND & ELLIS LLP). Legal suffixes and punctuation are
    # ignored only for detecting this placeholder; the role still needs unique
    # same-donor, same-employer evidence.
    occupation_key = occupation.fillna('').map(canonical_key)
    employer_key = employer.fillna('').map(canonical_key)
    company_as_occupation = indiv[
        occupation_key.ne('') & occupation_key.eq(employer_key) & real_employer
    ]
    n_fixed += _fill_placeholder_occupations(df, company_as_occupation)
    return n_fixed


# fill each empty occupation with the donor's most common one
def _fill_missing_occupations(df: pd.DataFrame, missing_occ: pd.DataFrame) -> int:
    """Fill each empty occupation with the donor's most common one there."""
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

        _set_derived_occupation(df, missing.index, counts.index[0])
        n_fixed += len(missing)
    return n_fixed


# replace placeholder occupations with the donor's one confirmed role
def _fill_placeholder_occupations(df: pd.DataFrame, placeholders: pd.DataFrame) -> int:
    """Replace placeholder occupations with the donor's one confirmed role."""
    n_fixed = 0
    for (dk, emp), rows in placeholders.groupby(['donor_key', 'contributor_employer']):
        occupation = _one_confirmed_role(df, dk, emp)
        if occupation is None:
            continue
        _set_derived_occupation(df, rows.index, occupation)
        n_fixed += len(rows)
    return n_fixed


# unify a donor's overlapping same-job occupation spellings at one employer
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


# replace vague SELF-EMPLOYED occupation with a real one from donor
def _fill_self_employed_occupation_from_donor(df: pd.DataFrame) -> int:
    """AW. 'SELF-EMPLOYED' filed as the OCCUPATION says nothing about the job; when the same donor
    filed a real occupation elsewhere (at the same employer first, otherwise anywhere), that one is used."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    occ = indiv['contributor_occupation'].fillna('').astype(str).str.strip().str.upper()
    vague = occ == 'SELF-EMPLOYED'
    if not vague.any():
        return 0
    real = indiv[(occ != '') & ~occ.isin(SKIP_OCCUPATIONS)]
    by_job = real.groupby(['donor_key', 'contributor_employer'])['contributor_occupation'].agg(lambda s: s.value_counts().index[0])
    by_donor = real.groupby('donor_key')['contributor_occupation'].agg(lambda s: s.value_counts().index[0])
    n_fixed = 0
    for idx in indiv.index[vague]:
        dk = df.at[idx, 'donor_key']
        emp = df.at[idx, 'contributor_employer']
        new = by_job.get((dk, emp)) if pd.notna(emp) else None
        if new is None:
            new = by_donor.get(dk)
        if new is None or new == 'SELF-EMPLOYED':
            continue
        df.at[idx, 'contributor_occupation'] = new
        df.at[idx, 'occupation_category'] = _categorize_final(pd.Series([new])).iloc[0]
        n_fixed += 1
    return n_fixed
