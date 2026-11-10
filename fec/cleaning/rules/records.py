"""Apply record cleaning rules in order."""
import pandas as pd

from fec.cleaning.reports.trail import (
    EMPLOYMENT_FIELDS,
    NAME_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.rules.employer_swaps import settle_job_in_employer
from fec.cleaning.work.retired import settle_retired_fields
from fec.cleaning.entities import (
    fix_employer_equals_occupation,
    normalize_business_names,
)
from fec.cleaning.name_fixes import (
    apply_name_corrections,
    fix_credential_in_name,
    fix_double_apostrophes,
    fix_fullname_in_both_fields,
    normalize_name_periods,
)
from fec.cleaning.work.clean import swap_job_and_company
from fec.cleaning.work.style import normalize_occupation_style_step
from fec.cleaning.rules.junk import clean_remaining_junk
from fec.cleaning.rules.steps import ALL_ROWS, SAFETY_RULES


_NAME_ONLY = ('contributor_name',)
_OCCUPATION_ONLY = ('contributor_occupation',)

# In order: transform, fields, audit name, reason.
_AUDITED_RULES = (
    (settle_retired_fields, EMPLOYMENT_FIELDS,
     'retired_split_after_reclassify', 'retired_marker_read_from_both_fields'),
    (normalize_business_names, _NAME_ONLY,
     'enh_normalize_business_names', 'documented_company_name'),
    (normalize_name_periods, _NAME_ONLY,
     'enh_normalize_name_periods', 'remove_periods_from_initials'),
    (fix_credential_in_name, NAME_FIELDS,
     'enh_fix_credential_in_name', 'strip_credential_between_last_first'),
    (apply_name_corrections, NAME_FIELDS,
     'enh_name_corrections', 'manual_correction'),
    (fix_double_apostrophes, NAME_FIELDS,
     'enh_fix_apostrophes', 'double_apostrophe_cleanup'),
    (swap_job_and_company, WORK_FIELDS,
     'enh_swap_occ_emp', 'occ_emp_swap_or_same_value'),
    (clean_remaining_junk, WORK_FIELDS,
     'enh_clean_junk', 'junk_cleanup'),
    (fix_fullname_in_both_fields, NAME_FIELDS,
     'enh_fix_fullname_both', 'fullname_copied_to_both_fields'),
    (fix_employer_equals_occupation,
     WORK_FIELDS,
     'enh_fix_emp_eq_occ', 'employer_equals_occupation'),
    (settle_job_in_employer, WORK_FIELDS,
     'enh_fix_occ_as_employer', 'occupation_word_in_employer'),
)


# In order: transform, audit name, reason, fields.
_LATE_STEPS = (
    # Fix swapped company names before changing occupation spelling.
    (normalize_occupation_style_step, 'enh_normalize_occupation_style',
     'separator_joiner_or_plural_style_unified', _OCCUPATION_ONLY),
)


def apply_record_rules(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Apply all record-level rules."""
    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.Series(dtype='object', index=df.index)
        trail.run(df, lambda frame: 0, 'previous_employer_column', 'column_created',
                  ('previous_employer',))

    for transform, fields, audit_name, reason in _AUDITED_RULES:
        df = trail.run(df, transform, audit_name, reason, fields)[0]

    _apply_safety_rules(df, trail)
    for transform, audit_name, reason, fields in _LATE_STEPS:
        result = trail.run(df, transform, audit_name, reason, fields)
        if isinstance(result, tuple):
            df = result[0]

    return df


def _apply_safety_rules(df: pd.DataFrame, trail: AuditTrail) -> None:
    """Run each safety rule on its declared rows."""
    individuals = df['is_individual'].astype(bool)

    for rule, scope, fields, reason in SAFETY_RULES:
        if scope == ALL_ROWS:
            transform = rule
        else:
            def transform(frame, rule=rule):
                return rule(frame, individuals)
        step = 'safety_' + rule.__name__.lstrip('_')
        trail.run(df, transform, step, reason, fields)
