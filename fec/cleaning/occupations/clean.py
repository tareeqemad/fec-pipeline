"""Full employer/occupation cleaning pipeline."""
import re

import numpy as np
import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _norm, _set_missing
from fec.cleaning.audit_trail import WORK_FIELDS
from fec.config.employers import EMPLOYER_NORMALIZE
from fec.config.occupation_rules.normalize import (
    OCCUPATION_CANONICAL,
    OCCUPATION_NORMALIZE,
)
from fec.config.occupation_rules.rules import (
    EMPLOYER_FROM_OCCUPATION,
    OCCUPATION_FROM_EMPLOYER,
    OCCUPATION_KEYWORDS,
    OCCUPATION_REFUSAL_INPUTS,
    SWAP_JOB_TITLES,
)
from fec.log import get_logger

from .employer_deep_clean import _deep_clean_employer
from .employer_groups import _canonicalize_employers
from .normalize import EMPLOYER_STATUS_TEXT as _EMPLOYER_STATUS_TEXT
from .normalize import (
    _categorize,
    _classify_committee_names,
    _normalize_text,
    map_occupation_fixes,
)

logger = get_logger(__name__)

_CORP_NAME_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bP\.?A\.?\s*$'
    r'|\bPARTNERS\b|\bGROUP\b|\bASSOCIATES\b|\bVENTURES\b|\bCAPITAL\b'
    r'|\bHOLDINGS\b|\bSERVICES\b|\bENTERPRISES\b'
    r'|\bUNIVERSITY\b|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bHOSPITAL\b|\bINSTITUTE\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bDEPARTMENT\b|\bBUREAU\b',
    re.IGNORECASE,
)

_COMPANY_IN_OCCUPATION_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bLTD\b'
    r'|\bCOMPANY\b|\bCORPORATION\b|\bHOLDINGS\b|\bGROUP\b'
    r'|\bPARTNERS\b|\bVENTURES\b|\bCAPITAL\b|\bFUND\b'
    r'|\bASSOCIATES\b|\bENTERPRISES\b|\bPROPERTIES\b|\bREALTY\b'
    r'|\bADVISORS\b|\bINSURANCE\b|\bINDUSTRIES\b|\bBROTHERS\b'
    r'|\bBANK\b|\bFINANCIAL\b|\bMEDIA\b|\bSYSTEMS\b'
    r'|\bTECHNOLOGIES\b|\bSOLUTIONS\b|\bSERVICES\b|\bMANAGEMENT\b'
    r'|\bTRUST\b|\bINTERNATIONAL\b|\bGLOBAL\b'
    r'|\b\w+\s*&\s*\w+\b'
    r'|& (?:PARTNERS|ASSOCIATES|CRUTCHER|DE LLANO|BUTLER)',
)
_ORGANIZATION_IN_OCCUPATION_RE = re.compile(
    r'\bHOSPITAL\b|\bUNIVERSITY\b|\bINSTITUTE\b'
    r'|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bBUREAU\b'
    r'|\bDEPARTMENT\b|\bMINISTRY\b|\bAIPAC\b|\bDMFI\b',
)


def _swap_fields(df: pd.DataFrame, indexes: pd.Index) -> None:
    occupation = df.loc[indexes, 'contributor_occupation'].copy()
    employer = df.loc[indexes, 'contributor_employer'].copy()
    df.loc[indexes, 'contributor_occupation'] = employer
    df.loc[indexes, 'contributor_employer'] = occupation
    df.loc[indexes, 'occupation_category'] = _categorize(employer)


def fix_remaining_swapped_occ_emp(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, int, int]:
    """Fix swaps exposed by earlier cleaning."""
    individuals = _indiv_idx(df)
    occupation = _norm(df.loc[individuals, 'contributor_occupation'])
    employer = _norm(df.loc[individuals, 'contributor_employer'])

    employer_is_job = employer.isin(OCCUPATION_KEYWORDS)
    company = occupation.str.contains(_COMPANY_IN_OCCUPATION_RE, na=False)
    company &= ~occupation.str.startswith('CORP ', na=False)
    company_swaps = individuals[company & employer_is_job]
    if len(company_swaps):
        _swap_fields(df, company_swaps)

    same = individuals[(occupation == employer) & employer_is_job]
    if len(same):
        df.loc[same, 'contributor_employer'] = 'SELF-EMPLOYED'

    organization = occupation.str.contains(
        _ORGANIZATION_IN_OCCUPATION_RE,
        na=False,
    )
    organization_swaps = individuals[organization & employer_is_job]
    if len(organization_swaps):
        _swap_fields(df, organization_swaps)

    swaps = len(company_swaps) + len(organization_swaps)
    return df, swaps, len(same)


def normalize_occupation_canonical(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, int]:
    """Map safe occupation variants."""
    individuals = _indiv_idx(df)
    occupation = df.loc[individuals, 'contributor_occupation']
    hits = individuals[occupation.isin(OCCUPATION_CANONICAL)]
    if len(hits):
        df.loc[hits, 'contributor_occupation'] = occupation[hits].map(
            OCCUPATION_CANONICAL
        )
    return df, len(hits)


def _cross_fill(df: pd.DataFrame) -> None:
    """Fill an obvious missing occupation or employer from its other field."""
    for employer, (occupation, category) in OCCUPATION_FROM_EMPLOYER.items():
        mask = (
            df['is_individual']
            & df['contributor_occupation'].isna()
            & (df['contributor_employer'] == employer)
        )
        df.loc[mask, 'contributor_occupation'] = occupation
        df.loc[mask, 'occupation_category'] = category
        df.loc[mask, 'occupation_status'] = 'DISCLOSED'

    mask = df['contributor_occupation'].notna() & df['contributor_employer'].isna()
    if mask.any():
        df.loc[mask, 'contributor_employer'] = (
            df.loc[mask, 'contributor_occupation'].map(EMPLOYER_FROM_OCCUPATION)
        )


def _fix_swapped_occ_emp(df: pd.DataFrame) -> None:
    """Swap a company in occupation with a job title in employer."""
    has_both = df['contributor_occupation'].notna() & df['contributor_employer'].notna()
    employer_is_job = df['contributor_employer'].isin(SWAP_JOB_TITLES)
    real_employers = set(df.loc[~employer_is_job, 'contributor_employer'].dropna())
    occupation_is_company = (
        df['contributor_occupation'].str.contains(_CORP_NAME_RE, na=False)
        | df['contributor_occupation'].isin(real_employers)
    )
    mask = has_both & occupation_is_company & employer_is_job
    if not mask.any():
        return

    _swap_fields(df, df.index[mask])


def _normalize_work_text(df: pd.DataFrame) -> int:
    df['contributor_employer'], n_emp = _normalize_text(
        df['contributor_employer'], EMPLOYER_NORMALIZE, digits_only_before=_EMPLOYER_STATUS_TEXT
    )
    df['contributor_occupation'], n_occ = _normalize_text(
        df['contributor_occupation'], OCCUPATION_NORMALIZE, collapse_retire=True
    )
    return n_emp + n_occ


def _derive_status_and_category(df: pd.DataFrame) -> int:
    """Initial occupation_status and category."""
    has_occ = df['contributor_occupation'].notna()
    has_emp = df['contributor_employer'].notna()
    df['occupation_status'] = 'MISSING'
    df.loc[has_occ & has_emp, 'occupation_status'] = 'DISCLOSED'
    df.loc[has_occ & ~has_emp, 'occupation_status'] = 'EMPLOYER_MISSING'
    df['occupation_category'] = _categorize(df['contributor_occupation'])
    return int(len(df))


def _apply_occupation_fixes(df: pd.DataFrame) -> int:
    """Known typo -> (occupation, category) fixes."""
    original = map_occupation_fixes(df, df.index)
    if original.empty:
        return 0
    # refusals keep NOT DISCLOSED; junk becomes NaN
    now_not_disclosed = df.loc[original.index, 'contributor_occupation'] == 'NOT DISCLOSED'
    is_refusal = original.index[now_not_disclosed & original.isin(OCCUPATION_REFUSAL_INPUTS)]
    is_junk = original.index[now_not_disclosed & ~original.isin(OCCUPATION_REFUSAL_INPUTS)]
    df.loc[is_refusal, 'occupation_status'] = 'NOT_DISCLOSED'
    _set_missing(df, is_junk)
    return len(original)


def _clear_committee_work_fields(df: pd.DataFrame) -> int:
    """Committees: committee_type from the name, no occupation."""
    is_committee = ~df['is_individual']
    df['committee_type'] = pd.Series(dtype='object', index=df.index)
    if not is_committee.any():
        return 0
    df.loc[is_committee, 'committee_type'] = _classify_committee_names(
        df.loc[is_committee, 'contributor_name']
    )
    df.loc[is_committee, 'occupation_category'] = 'POLITICAL COMMITTEE'
    # N/A would read back as NaN
    df.loc[is_committee, 'occupation_status'] = 'NOT_APPLICABLE'
    df.loc[is_committee, 'contributor_occupation'] = np.nan
    return int(is_committee.sum())


def _mark_still_missing(df: pd.DataFrame) -> int:
    """Individuals still missing stay NaN."""
    still_missing_occ = df['is_individual'] & df['contributor_occupation'].isna()
    df.loc[still_missing_occ, 'occupation_category'] = pd.NA
    df.loc[still_missing_occ, 'occupation_status'] = 'MISSING'
    return int(still_missing_occ.sum())


def clean_employer_occupation(df: pd.DataFrame, trail) -> tuple[pd.DataFrame, dict[str, int]]:
    """Run the employer/occupation pipeline in place."""
    counts = {'normalized': 0, 'occ_fixed': 0, 'comm_filled': 0}

    counts['normalized'] += trail.run(
        df, _normalize_work_text, 'occ_normalize_text',
        'text_normalized_or_missing_placeholder_nulled', WORK_FIELDS,
    )
    # swap before short titles are dropped
    trail.run(df, _fix_swapped_occ_emp, 'occ_swap_fields', 'occupation_and_employer_swapped', WORK_FIELDS)
    counts['normalized'] += _deep_clean_employer(df, trail)
    counts['normalized'] += trail.run(
        df, _canonicalize_employers, 'occ_canonicalize_employers',
        'employer_variant_unified_by_group_key', WORK_FIELDS,
    )
    trail.run(df, _derive_status_and_category, 'occ_derive_status_category', 'derived_from_occupation', WORK_FIELDS)
    counts['occ_fixed'] = trail.run(
        df, _apply_occupation_fixes, 'occ_known_fixes',
        'known_occupation_typo_fixed_or_junk_nulled', WORK_FIELDS,
    )
    counts['comm_filled'] = trail.run(
        df, _clear_committee_work_fields, 'occ_committee_rows', 'committee_has_no_occupation', WORK_FIELDS,
    )
    trail.run(df, _cross_fill, 'occ_cross_fill', 'filled_from_paired_field', WORK_FIELDS)
    trail.run(df, _mark_still_missing, 'occ_mark_missing', 'occupation_missing', WORK_FIELDS)
    return df, counts
