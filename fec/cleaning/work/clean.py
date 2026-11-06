"""Full employer/occupation cleaning pipeline."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _map_occupations, _norm
from fec.cleaning.reports.trail import WORK_FIELDS
from fec.cleaning.work.employer_deep_clean import _deep_clean_employer
from fec.cleaning.work.normalize import (
    EMPLOYER_STATUS_TEXT as _EMPLOYER_STATUS_TEXT,
    _categorize,
    _classify_committee_names,
    _normalize_text,
    map_occupation_fixes,
    swap_employer_and_occupation,
)
from fec.config.occupation_rules.rules import (
    EMPLOYER_NORMALIZE,
    OCCUPATION_CANONICAL,
    OCCUPATION_NORMALIZE,
    EMPLOYER_FROM_OCCUPATION,
    OCCUPATION_FROM_EMPLOYER,
    OCCUPATION_KEYWORDS,
    SWAP_JOB_TITLES,
    KNOWN_COMPANY_OCCUPATIONS,
)
from fec.config.constants import STATUS_CATEGORIES
from fec.cleaning.work.status import is_job_title
from fec.cleaning.work.employer_aliases import employer_rule_key

# A company or institution name in occupation text: "INC", "GROUP", "HOSPITAL".
_COMPANY_IN_OCCUPATION_RE = re.compile(
    r'\bINC\b\.?|\bP\.?A\.?\s*$'
    r'|\b(?:LLC|LLP|CORP|LTD|COMPANY|CORPORATION|HOLDINGS|GROUP'
    r'|PARTNERS|VENTURES|CAPITAL|FUND|ASSOCIATES|ENTERPRISES|PROPERTIES|REALTY'
    r'|ADVISORS|INSURANCE|INDUSTRIES|BROTHERS|BANK|FINANCIAL|MEDIA|SYSTEMS'
    r'|TECHNOLOGIES|SOLUTIONS|SERVICES|MANAGEMENT|TRUST|INTERNATIONAL|GLOBAL'
    r'|HOSPITAL|UNIVERSITY|INSTITUTE|COLLEGE|SCHOOL|ACADEMY'
    r'|FOUNDATION|AGENCY|BUREAU|DEPARTMENT|MINISTRY|AIPAC|DMFI)\b'
    r'|\b\w+\s*&\s*\w+\b'
    r'|& (?:PARTNERS|ASSOCIATES|CRUTCHER|DE LLANO|BUTLER)',
    re.IGNORECASE,
)
_SWAP_JOBS = SWAP_JOB_TITLES | OCCUPATION_KEYWORDS


def swap_job_and_company(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Employer holds a job and occupation a company: swap the two.

    A job title in the occupation is never treated as a company, and
    SELF-EMPLOYED in the occupation is swapped back to the employer.
    """
    # Filings first typed as committees get their filed work back later.
    rows = df.index[df['is_individual'].astype(bool)]
    employer = _norm(df.loc[rows, 'contributor_employer'])
    occupation = _norm(df.loc[rows, 'contributor_occupation'])

    employer_is_job = employer.isin(_SWAP_JOBS)
    seen_as_employer = set(
        employer[~employer_is_job & ~employer.isin(STATUS_CATEGORIES)].map(employer_rule_key)
    )
    company = (
        occupation.str.contains(_COMPANY_IN_OCCUPATION_RE, na=False)
        | occupation.isin(KNOWN_COMPANY_OCCUPATIONS)
        | occupation.map(employer_rule_key).isin(seen_as_employer)
    ) & ~occupation.str.startswith('CORP ', na=False)
    occupation_is_job = occupation.isin(_SWAP_JOBS) | occupation.map(is_job_title)
    swap = rows[
        employer_is_job & occupation.ne('')
        & ((company & ~occupation_is_job) | occupation.eq('SELF-EMPLOYED'))
    ]
    if len(swap):
        swap_employer_and_occupation(df, swap, canonical=False)
    return df, len(swap)


def normalize_occupation_canonical(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Map safe occupation variants."""
    return _map_occupations(df, OCCUPATION_CANONICAL)


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
        df['contributor_occupation'], OCCUPATION_NORMALIZE, collapse_retire=True
    )


def _derive_status_and_category(df: pd.DataFrame) -> None:
    """Initial occupation category."""
    df['occupation_category'] = _categorize(df['contributor_occupation'])


# Apply known occupation fixes.
def _apply_occupation_fixes(df: pd.DataFrame) -> None:
    map_occupation_fixes(df, df.index)


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


def clean_employer_occupation(df: pd.DataFrame, trail) -> None:
    """Run the employer/occupation pipeline in place."""
    trail.run(
        df, _normalize_work_text, 'occ_normalize_text',
        'text_normalized_or_missing_placeholder_nulled', WORK_FIELDS,
    )
    # swap before short titles are dropped
    trail.run(df, swap_job_and_company, 'occ_swap_fields', 'occupation_and_employer_swapped', WORK_FIELDS)
    _deep_clean_employer(df, trail)
    trail.run(df, _derive_status_and_category, 'occ_derive_status_category', 'derived_from_occupation', WORK_FIELDS)
    trail.run(
        df, _apply_occupation_fixes, 'occ_known_fixes',
        'known_occupation_typo_fixed_or_junk_nulled', WORK_FIELDS,
    )
    trail.run(
        df, _clear_committee_work_fields, 'occ_committee_rows', 'committee_has_no_occupation', WORK_FIELDS,
    )
    trail.run(df, _cross_fill, 'occ_cross_fill', 'filled_from_paired_field', WORK_FIELDS)
