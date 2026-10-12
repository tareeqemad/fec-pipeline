"""Keep occupation_category consistent with employer, occupation."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.occupations.normalize import categorize_occupation, categorize_final
from fec.config.constants import NOT_EMPLOYED_VARIANTS, SKIP_EMPLOYERS

# status employer and matching occupations, in order
_STATUS_OCCUPATIONS = (
    ('RETIRED', {'RETIRED', ''}),
    ('NOT EMPLOYED', {'NOT EMPLOYED', 'UNEMPLOYED', ''}),
    ('HOMEMAKER', {'HOMEMAKER', 'HOUSEWIFE', ''}),
)


# keep employer, occupation and category mutually consistent
def fix_emp_occ_category_consistency(df: pd.DataFrame) -> int:
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    occ = df['contributor_occupation'].fillna('')
    categories = df['occupation_category'].fillna('')
    n_fixed = 0

    # RETIRED occupation beats NOT EMPLOYED employer
    retired_not_employed = (
        is_indiv
        & emp.isin(NOT_EMPLOYED_VARIANTS)
        & occ.eq('RETIRED')
        & categories.eq('RETIRED')
    )
    n_retired_not_employed = int(retired_not_employed.sum())
    if n_retired_not_employed:
        df.loc[retired_not_employed, 'contributor_employer'] = 'RETIRED'
        df.loc[retired_not_employed, 'occupation_status'] = 'NOT_APPLICABLE'
        n_fixed += n_retired_not_employed

    # status employer sets matching occupation and category
    for status, occupations in _STATUS_OCCUPATIONS:
        wrong_cat = (
            is_indiv & (emp == status)
            & (categories != status)
            & occ.isin(occupations)
        )
        n_status = int(wrong_cat.sum())
        if n_status:
            df.loc[wrong_cat, 'contributor_occupation'] = status
            df.loc[wrong_cat, 'occupation_category'] = status
            df.loc[wrong_cat, 'occupation_status'] = 'NOT_APPLICABLE'
            n_fixed += n_status

    # real-company employer beats SELF-EMPLOYED category
    se_cat_real_emp = (
        is_indiv & (categories == 'SELF-EMPLOYED')
        & ~emp.isin(SKIP_EMPLOYERS) & (emp != '')
    )
    if se_cat_real_emp.any():
        real_occ = df.loc[se_cat_real_emp, 'contributor_occupation'].fillna('')
        new_cats = categorize_occupation(real_occ)
        needs_fix = se_cat_real_emp & (new_cats != 'SELF-EMPLOYED') & new_cats.notna()
        n_se_cat = int(needs_fix.sum())
        if n_se_cat:
            df.loc[needs_fix, 'occupation_category'] = new_cats[needs_fix]
            df.loc[needs_fix, 'occupation_status'] = 'DISCLOSED'
            n_fixed += n_se_cat

    return n_fixed


# categorize slash occupations using their first part
def fix_slash_occupation(df: pd.DataFrame) -> int:
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
    new_cats = categorize_occupation(first_part)
    improved = has_slash & new_cats.notna() & (new_cats != 'OTHER')
    n_fixed = int(improved.sum())
    if n_fixed:
        df.loc[improved, 'occupation_category'] = new_cats[improved]
    return n_fixed


# reclassify OTHER categories from final occupation
def reclassify_other_category(df: pd.DataFrame) -> int:
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
