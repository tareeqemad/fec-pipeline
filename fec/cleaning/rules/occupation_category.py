"""Keep occupation_category consistent with the employer and occupation."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.work.normalize import _categorize, categorize_final
from fec.config.constants import NOT_EMPLOYED_VARIANTS, SKIP_EMPLOYERS

# (status employer, occupations already meaning it), in order.
_STATUS_OCCUPATIONS = (
    ('RETIRED', {'RETIRED', ''}),
    ('NOT EMPLOYED', {'NOT EMPLOYED', 'UNEMPLOYED', ''}),
    ('HOMEMAKER', {'HOMEMAKER', 'HOUSEWIFE', ''}),
)


def _fix_emp_occ_category_consistency(df: pd.DataFrame) -> int:
    """Align work statuses and recategorize occupations at real employers."""
    individuals = df['entity_type'] == 'INDIVIDUAL'
    # Match against the values at the start of this rule.
    employers = df['contributor_employer'].fillna('')
    occupations = df['contributor_occupation'].fillna('')
    categories = df['occupation_category'].fillna('')

    # An explicit RETIRED occupation is more specific than NOT EMPLOYED.
    retired_not_employed = (
        individuals
        & employers.isin(NOT_EMPLOYED_VARIANTS)
        & occupations.eq('RETIRED')
        & categories.eq('RETIRED')
    )
    n_fixed = int(retired_not_employed.sum())
    if n_fixed:
        df.loc[retired_not_employed, 'contributor_employer'] = 'RETIRED'
        df.loc[retired_not_employed, 'occupation_status'] = 'NOT_APPLICABLE'

    # Match the category when occupation is empty or agrees.
    for status, allowed_occupations in _STATUS_OCCUPATIONS:
        wrong_category = (
            individuals & (employers == status)
            & (categories != status)
            & occupations.isin(allowed_occupations)
        )
        count = int(wrong_category.sum())
        if not count:
            continue
        df.loc[wrong_category, 'contributor_occupation'] = status
        df.loc[wrong_category, 'occupation_category'] = status
        df.loc[wrong_category, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += count

    # For a real employer, the category follows the occupation.
    self_employed_category = (
        individuals & (categories == 'SELF-EMPLOYED')
        & ~employers.isin(SKIP_EMPLOYERS) & (employers != '')
    )
    if not self_employed_category.any():
        return n_fixed

    current_occupations = df.loc[self_employed_category, 'contributor_occupation'].fillna('')
    new_categories = _categorize(current_occupations)
    needs_fix = self_employed_category & (new_categories != 'SELF-EMPLOYED') & new_categories.notna()
    count = int(needs_fix.sum())
    if count:
        df.loc[needs_fix, 'occupation_category'] = new_categories[needs_fix]
        df.loc[needs_fix, 'occupation_status'] = 'DISCLOSED'
        n_fixed += count
    return n_fixed


def _fix_slash_occupation(df: pd.DataFrame) -> int:
    """Use the first slash-separated occupation when the category is missing."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    occ = df['contributor_occupation'].fillna('')
    categories = df['occupation_category'].fillna('')

    has_slash = (
        is_indiv
        & occ.str.contains('/', na=False, regex=False)
        & categories.isin({'OTHER', ''})
    )
    n_candidates = int(has_slash.sum())
    if not n_candidates:
        return 0

    first_part = occ[has_slash].str.split('/').str[0].str.strip()
    new_cats = _categorize(first_part)
    improved = has_slash & new_cats.notna() & (new_cats != 'OTHER')
    n_fixed = int(improved.sum())
    if n_fixed:
        df.loc[improved, 'occupation_category'] = new_cats[improved]
    return n_fixed


def _reclassify_other_category(df: pd.DataFrame) -> int:
    """Replace OTHER categories using the final occupation text."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    is_other = is_indiv & (df['occupation_category'] == 'OTHER')
    if not is_other.any():
        return 0

    new_categories = categorize_final(
        df.loc[is_other, 'contributor_occupation']
    )
    improved = new_categories.ne('OTHER')
    df.loc[new_categories.index[improved], 'occupation_category'] = (
        new_categories.loc[improved]
    )
    return int(improved.sum())
