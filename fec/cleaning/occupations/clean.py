"""Full employer/occupation cleaning pipeline."""
import numpy as np
import pandas as pd

from fec.config import (
    EMPLOYER_NORMALIZE,
    OCCUPATION_FIXES,
    OCCUPATION_NORMALIZE,
    OCCUPATION_REFUSAL_INPUTS,
)

from .normalize import _normalize_text, _categorize, _classify_committee_names
from .crossfill import _cross_fill, _fix_swapped_occ_emp
from .employer_deep_clean import _deep_clean_employer
from .canonical import _canonicalize_employers

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
