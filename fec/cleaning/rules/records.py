"""Apply record cleaning rules in order."""
import pandas as pd

from fec.cleaning.entities import fix_employer_equals_occupation, normalize_business_names
from fec.cleaning.name_fixes import (
    apply_name_corrections,
    fix_credential_in_name,
    fix_double_apostrophes,
    fix_fullname_in_both_fields,
    normalize_name_periods,
)
from fec.cleaning.rules.employer import _fix_choose_prefix, _null_sector_as_employer
from fec.cleaning.rules.employer_swaps import settle_job_in_employer
from fec.cleaning.rules.junk import clean_remaining_junk
from fec.cleaning.rules.name_fields import _fix_own_name_as_employer
from fec.cleaning.rules.occupation import (
    _fill_null_occupation_category,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
)
from fec.cleaning.rules.occupation_context import _disambiguate_vague_occupation
from fec.cleaning.work.clean import swap_job_and_company
from fec.cleaning.work.retired import settle_retired_fields
from fec.cleaning.work.style import normalize_occupation_style_step

# In order.
_RULES = (
    settle_retired_fields,
    normalize_business_names,
    normalize_name_periods,
    fix_credential_in_name,
    apply_name_corrections,
    fix_double_apostrophes,
    swap_job_and_company,
    clean_remaining_junk,
    fix_fullname_in_both_fields,
    fix_employer_equals_occupation,
    settle_job_in_employer,
    lambda df: _fill_null_occupation_category(df, df['is_individual'].astype(bool)),
    _fix_choose_prefix,
    _null_sector_as_employer,
    _fix_own_name_as_employer,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
    _disambiguate_vague_occupation,
    # Fix swapped company names before changing occupation spelling.
    normalize_occupation_style_step,
)


def apply_record_rules(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all record-level rules."""
    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.Series(dtype='object', index=df.index)
    for rule in _RULES:
        result = rule(df)
        if isinstance(result, tuple):
            df = result[0]
    return df
