"""Full employer/occupation cleaning pipeline."""
import re
from collections import defaultdict

import numpy as np
import pandas as pd

from fec.config.constants import CANONICAL_EMPLOYER_SKIP_VALUES
from fec.config.employers import EMPLOYER_NORMALIZE
from fec.config.occupation_rules.rules import (
    EMPLOYER_FROM_OCCUPATION,
    OCCUPATION_FIXES,
    OCCUPATION_FROM_EMPLOYER,
    OCCUPATION_NORMALIZE,
    OCCUPATION_REFUSAL_INPUTS,
    SWAP_JOB_TITLES,
)
from fec.log import get_logger

from .normalize import _normalize_text, _categorize, _classify_committee_names
from .employer_deep_clean import _deep_clean_employer

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
_DOTTED_SUFFIX_RE = re.compile(r',?\s*\b(P\.C\.?|P\.A\.?)\s*$')
_CORP_SUFFIX_RE = re.compile(
    r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PC|PA|PLLC|LP)\s*$'
)
_PA_PC_PROTECT_RE = re.compile(
    r'\b(CPA|DPA|RPA|EPA|SEPA|SHERPA)\s+(PA|PC)\s*$',
    re.IGNORECASE,
)


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

    old_occupation = df.loc[mask, 'contributor_occupation'].copy()
    old_employer = df.loc[mask, 'contributor_employer'].copy()
    df.loc[mask, 'contributor_occupation'] = old_employer
    df.loc[mask, 'contributor_employer'] = old_occupation
    df.loc[mask, 'occupation_category'] = _categorize(
        df.loc[mask, 'contributor_occupation']
    )


def _employer_group_key(name: str) -> str:
    """Build a strict key for employer spelling variants."""
    key = re.sub(r'([A-Z]),([A-Z])', r'\1\2', name.strip().upper())
    key = re.sub(r'[,\.\s]+', ' ', key)
    key = _DOTTED_SUFFIX_RE.sub('', key).strip()
    if not _PA_PC_PROTECT_RE.search(key):
        key = _CORP_SUFFIX_RE.sub('', key).strip()
    else:
        key = re.sub(
            r',?\s*\b(INC|LLC|LLP|LTD|CORP|CORPORATION|COMPANY|CO|PLLC|LP)\s*$',
            '', key,
        ).strip()
    key = re.sub(r'^\s*THE\s+', '', key).replace('&', 'AND')
    return re.sub(r'\s+', '', key)


def _canonicalize_employers(df: pd.DataFrame) -> int:
    """Unify punctuation, suffix and spacing variants of employer names."""
    employer = df['contributor_employer']
    mask = employer.notna() & ~employer.isin(CANONICAL_EMPLOYER_SKIP_VALUES)
    active = employer[mask]
    if active.empty:
        return 0

    mid_comma = active.str.contains(r'[A-Z],[A-Z]', na=False, regex=True)
    if mid_comma.any():
        indexes = mid_comma[mid_comma].index
        df.loc[indexes, 'contributor_employer'] = active[mid_comma].str.replace(
            r'([A-Z]),([A-Z])', r'\1\2', regex=True,
        )
        active = df.loc[mask, 'contributor_employer']

    dotted = active.str.contains(r'\bP\.[CA]\.?\s*$', na=False, regex=True)
    if dotted.any():
        indexes = dotted[dotted].index
        df.loc[indexes, 'contributor_employer'] = (
            active[dotted]
            .str.replace(r',?\s*\bP\.C\.?\s*$', ' PC', regex=True)
            .str.replace(r',?\s*\bP\.A\.?\s*$', ' PA', regex=True)
            .str.strip()
        )
        active = df.loc[mask, 'contributor_employer']

    counts = active.value_counts()
    groups = defaultdict(list)
    for name in counts.index:
        key = _employer_group_key(name)
        if key:
            groups[key].append(name)

    mapping = {}
    for variants in groups.values():
        if len(variants) < 2:
            continue
        canonical = max(variants, key=lambda variant: counts[variant])
        mapping.update(
            {variant: canonical for variant in variants if variant != canonical}
        )

    if not mapping:
        return 0

    to_fix = employer.isin(mapping)
    changed = int(to_fix.sum())
    df.loc[to_fix, 'contributor_employer'] = employer[to_fix].map(mapping)
    logger.info(
        "Canonicalized %d employer variants across %d groups -> %d rows updated",
        len(mapping), sum(len(group) > 1 for group in groups.values()), changed,
    )
    return changed


def clean_employer_occupation(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Run the full employer/occupation pipeline; modifies df in-place, returns (df, change counts)."""
    counts = {
        'normalized': 0,
        'occ_fixed': 0,
        'comm_filled': 0,
    }

    # 1. normalize text
    df['contributor_employer'], n_emp = _normalize_text(
        df['contributor_employer'], EMPLOYER_NORMALIZE
    )
    df['contributor_occupation'], n_occ = _normalize_text(
        df['contributor_occupation'], OCCUPATION_NORMALIZE, collapse_retire=True
    )
    counts['normalized'] = n_emp + n_occ

    # 1b. Swap first so short titles such as VP are not discarded before
    # the company sitting in the occupation field can be recovered.
    _fix_swapped_occ_emp(df)

    # 1c. employer-specific deep cleaning
    n_emp_deep = _deep_clean_employer(df)
    counts['normalized'] += n_emp_deep

    # 1d. canonicalize employer name variants
    n_canon = _canonicalize_employers(df)
    counts['normalized'] += n_canon

    # 1d. initial occupation_status (NaN here = raw was NULL/empty/junk)
    has_occ = df['contributor_occupation'].notna()
    has_emp = df['contributor_employer'].notna()
    df['occupation_status'] = 'MISSING'
    df.loc[has_occ & has_emp, 'occupation_status'] = 'DISCLOSED'
    df.loc[has_occ & ~has_emp, 'occupation_status'] = 'EMPLOYER_MISSING'

    # 2. categorize by regex rules
    df['occupation_category'] = _categorize(df['contributor_occupation'])

    # 3. known typo -> (occupation, category) fixes
    needs_fix = df['contributor_occupation'].isin(OCCUPATION_FIXES)
    if needs_fix.any():
        counts['occ_fixed'] = int(needs_fix.sum())
        original = df.loc[needs_fix, 'contributor_occupation']
        df.loc[needs_fix, 'contributor_occupation'] = original.map(
            {key: value[0] for key, value in OCCUPATION_FIXES.items()}
        )
        df.loc[needs_fix, 'occupation_category'] = original.map(
            {key: value[1] for key, value in OCCUPATION_FIXES.items()}
        )

        # explicit refusals keep the "NOT DISCLOSED" text; junk becomes NaN
        fixed_to_not_disclosed = needs_fix & (df['contributor_occupation'] == 'NOT DISCLOSED')
        is_refusal = fixed_to_not_disclosed & original.isin(OCCUPATION_REFUSAL_INPUTS)
        is_junk = fixed_to_not_disclosed & ~original.isin(OCCUPATION_REFUSAL_INPUTS)
        df.loc[is_refusal, 'occupation_status'] = 'NOT_DISCLOSED'
        df.loc[is_junk, 'contributor_occupation'] = np.nan
        df.loc[is_junk, 'occupation_category'] = pd.NA
        df.loc[is_junk, 'occupation_status'] = 'MISSING'

    # 4. committees: committee_type from the name, no occupation
    is_committee = ~df['is_individual']
    df['committee_type'] = pd.Series(dtype='object', index=df.index)

    if is_committee.any():
        df.loc[is_committee, 'committee_type'] = _classify_committee_names(
            df.loc[is_committee, 'contributor_name']
        )
        counts['comm_filled'] = int(is_committee.sum())

        df.loc[is_committee, 'occupation_category'] = 'POLITICAL COMMITTEE'

        # NOT_APPLICABLE, not 'N/A': pandas reads N/A as NaN in CSV I/O
        df.loc[is_committee, 'occupation_status'] = 'NOT_APPLICABLE'

        df.loc[is_committee, 'contributor_occupation'] = np.nan

    # 5. cross-fill occupation <-> employer
    _cross_fill(df)

    # 6. individuals still missing stay NaN; only explicit refusals got "NOT DISCLOSED"
    still_missing_occ = df['is_individual'] & df['contributor_occupation'].isna()
    df.loc[still_missing_occ, 'occupation_category'] = pd.NA
    df.loc[still_missing_occ, 'occupation_status'] = 'MISSING'

    return df, counts
