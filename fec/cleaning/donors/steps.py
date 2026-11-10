"""Keep each donor consistent across filings."""
import pandas as pd

from fec.config.occupation_rules.rules import EMPLOYER_FROM_CATEGORY, FINAL_EMPLOYER_FROM_OCCUPATION
from fec.cleaning.donors.entity import _apply_entity_overrides
from fec.cleaning.donors.occupation import (
    _fill_occupation_from_donor,
    _rederive_occupation_category,
)
from fec.cleaning.donors.retired import (
    _settle_retired_employer,
)
from fec.cleaning.rules.manual import apply_manual_employer_overrides


def _fill_employer_from_occupation(df: pd.DataFrame) -> int:
    """Fill work statuses from the same filing, inferring no company."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    empty_emp = df['contributor_employer'].isna() | (df['contributor_employer'] == '')
    occ = df['contributor_occupation'].fillna('')

    n = 0
    for occ_val, emp_val in FINAL_EMPLOYER_FROM_OCCUPATION.items():
        mask = is_indiv & empty_emp & (occ == occ_val)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    # Still empty? Set from occupation_category
    still_empty = is_indiv & (df['contributor_employer'].isna() | (df['contributor_employer'] == ''))
    for cat, emp_val in EMPLOYER_FROM_CATEGORY.items():
        mask = still_empty & (df['occupation_category'] == cat)
        cnt = int(mask.sum())
        if cnt:
            df.loc[mask, 'contributor_employer'] = emp_val
            n += cnt

    return n

# In order.
CONSISTENCY_FIXES = (
    _fill_occupation_from_donor,
    _fill_employer_from_occupation,
    _apply_entity_overrides,
    _rederive_occupation_category,
    _settle_retired_employer,
)


def apply_donor_consistency(df: pd.DataFrame) -> None:
    """Repair values using donor history; reviewed decisions win."""
    apply_manual_employer_overrides(df, company_names_only=True)
    for fix in CONSISTENCY_FIXES:
        fix(df)
    apply_manual_employer_overrides(df, company_names_only=True)
