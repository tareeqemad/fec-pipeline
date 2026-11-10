"""Full employer/occupation cleaning pipeline."""
import numpy as np
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.work.retired import settle_retired_fields
from fec.cleaning.work.employer_deep_clean import DEEP_CLEAN_PASSES
from fec.cleaning.work.normalize import (
    EMPLOYER_STATUS_TEXT as _EMPLOYER_STATUS_TEXT,
    _categorize,
    _classify_committee_names,
    _normalize_text,
    swap_employer_and_occupation,
)
from fec.config.occupation_rules.rules import (
    EMPLOYER_NORMALIZE,
    OCCUPATION_NORMALIZE,
    EMPLOYER_FROM_OCCUPATION,
    OCCUPATION_FROM_EMPLOYER,
    OCCUPATION_KEYWORDS,
    SWAP_JOB_TITLES,
)
from fec.cleaning.work.company import COMPANY_LEVELS, company_evidence, filers_of

_SWAP_JOBS = SWAP_JOB_TITLES | OCCUPATION_KEYWORDS


def occupation_is_company(employer, occupation, jobs, filers=None) -> pd.Series:
    """True where the occupation names a company (see fec.cleaning.work.company)."""
    # Beside a job in the employer box, one other filing naming the value
    # as an employer is evidence enough.
    levels = company_evidence(occupation, employers=employer, filers=filers, min_filers=1)
    return levels.isin(COMPANY_LEVELS) & ~occupation.isin(jobs)


def swap_job_and_company(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Employer holds a job and occupation a company: swap the two.

    SELF-EMPLOYED in the occupation is swapped back to the employer too.
    """
    # Filings first typed as committees get their filed work back later.
    rows = df.index[df['is_individual'].astype(bool)]
    employer = _norm(df.loc[rows, 'contributor_employer'])
    occupation = _norm(df.loc[rows, 'contributor_occupation'])

    filers = filers_of(df, rows)
    swap = rows[
        employer.isin(_SWAP_JOBS)
        & (occupation_is_company(employer, occupation, _SWAP_JOBS, filers) | occupation.eq('SELF-EMPLOYED'))
    ]
    if len(swap):
        swap_employer_and_occupation(df, swap, canonical=False)
    return df, len(swap)


def _cross_fill(df: pd.DataFrame) -> None:
    """Fill an obviously missing occupation or employer from the other."""
    for employer, (occupation, category) in OCCUPATION_FROM_EMPLOYER.items():
        mask = (
            df['is_individual']
            & df['contributor_occupation'].isna()
            & (df['contributor_employer'] == employer)
        )
        df.loc[mask, 'contributor_occupation'] = occupation
        df.loc[mask, 'occupation_category'] = category

    mask = df['contributor_occupation'].notna() & df['contributor_employer'].isna()
    if mask.any():
        df.loc[mask, 'contributor_employer'] = (
            df.loc[mask, 'contributor_occupation'].map(EMPLOYER_FROM_OCCUPATION)
        )


def _normalize_work_text(df: pd.DataFrame) -> None:
    df['contributor_employer'] = _normalize_text(
        df['contributor_employer'], EMPLOYER_NORMALIZE, digits_only_before=_EMPLOYER_STATUS_TEXT
    )
    df['contributor_occupation'] = _normalize_text(
        df['contributor_occupation'], OCCUPATION_NORMALIZE
    )


def _derive_status_and_category(df: pd.DataFrame) -> None:
    """Initial occupation category."""
    df['occupation_category'] = _categorize(df['contributor_occupation'])


def _clear_committee_work_fields(df: pd.DataFrame) -> None:
    """Committees: committee_type from the name, no occupation."""
    is_committee = ~df['is_individual']
    df['committee_type'] = pd.Series(dtype='object', index=df.index)
    if not is_committee.any():
        return
    df.loc[is_committee, 'committee_type'] = _classify_committee_names(
        df.loc[is_committee, 'contributor_name']
    )
    df.loc[is_committee, 'occupation_category'] = 'POLITICAL COMMITTEE'
    # N/A would read back as NaN
    df.loc[is_committee, 'contributor_occupation'] = np.nan


def clean_employer_occupation(df: pd.DataFrame) -> None:
    """Run the employer/occupation pipeline in place."""
    _normalize_work_text(df)
    # swap before short titles are dropped
    settle_retired_fields(df)
    swap_job_and_company(df)
    for clean in DEEP_CLEAN_PASSES:
        clean(df)
    _derive_status_and_category(df)
    _clear_committee_work_fields(df)
    _cross_fill(df)
