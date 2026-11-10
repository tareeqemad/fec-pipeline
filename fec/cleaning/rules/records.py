"""Apply record cleaning rules in order."""
import pandas as pd

from fec.cleaning.entities import normalize_business_names
from fec.cleaning.name_fixes import (
    apply_name_corrections,
    fix_credential_in_name,
    fix_double_apostrophes,
    fix_fullname_in_both_fields,
    normalize_name_periods,
)
from fec.cleaning.rules.occupation_context import _disambiguate_vague_occupation
from fec.cleaning.work.read import read_work

# In order.
_NAME_RULES = (
    normalize_business_names,
    normalize_name_periods,
    fix_credential_in_name,
    apply_name_corrections,
    fix_double_apostrophes,
    fix_fullname_in_both_fields,
)


def apply_record_rules(df: pd.DataFrame, filed_employers: pd.Series, filed_occupations: pd.Series) -> pd.DataFrame:
    """Settle names, then read every individual's work fields from the filing."""
    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    for rule in _NAME_RULES:
        df = rule(df)[0]
    read_work(df, filed_employers, filed_occupations)
    df.loc[df['entity_type'].eq('COMMITTEE/PAC') & df['occupation_category'].isna(), 'occupation_category'] = 'POLITICAL COMMITTEE'
    _disambiguate_vague_occupation(df)
    return df
