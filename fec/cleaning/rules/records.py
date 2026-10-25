"""Apply record cleaning rules in order."""
from collections.abc import Callable

import pandas as pd

from fec.cleaning.reports.trail import (
    EMPLOYMENT_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.work.employer_names import (
    apply_employer_synonyms,
    fix_occupation_as_employer,
)
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names
from fec.cleaning.work.employer_normalize import normalize_employer_canonical
from fec.cleaning.entities import (
    apply_name_corrections,
    fix_credential_in_name,
    fix_double_apostrophes,
    fix_employer_equals_occupation,
    fix_fullname_in_both_fields,
    fix_misclassified_business_entities,
    fix_remaining_misclassified,
    normalize_business_names,
    normalize_name_periods,
)
from fec.cleaning.work.clean import (
    fix_remaining_swapped_occ_emp,
    normalize_occupation_canonical,
)
from fec.cleaning.work.style import (
    apply_occupation_typo_fixes,
    normalize_occupation_style_step,
)
from fec.cleaning.rules.junk import (
    _clean_junk_status_word_employer,
    _clean_self_employed_variants,
    clean_remaining_junk,
)
from fec.cleaning.rules.steps import ALL_ROWS, INDIVIDUALS, SAFETY_RULES
from fec.log import get_logger

logger = get_logger(__name__)


_CLASSIFICATION_FIELDS = ENTITY_FIELDS + (
    'contributor_first_name', 'contributor_last_name',
    'occupation_category', 'contributor_occupation',
)
_NAME_ONLY = ('contributor_name',)
_OCCUPATION_ONLY = ('contributor_occupation',)
_EMPLOYER_ONLY = ('contributor_employer',)

# In order: transform, fields, audit name, reason.
_AUDITED_RULES = (
    (fix_remaining_misclassified, _CLASSIFICATION_FIELDS,
     'enh_reclassify_committee', 'committee_name_pattern'),
    (fix_misclassified_business_entities, _CLASSIFICATION_FIELDS,
     'enh_reclassify_business', 'business_suffix_or_org_pattern'),
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
    (fix_remaining_swapped_occ_emp, WORK_FIELDS,
     'enh_swap_occ_emp', 'occ_emp_swap_or_same_value'),
    (normalize_occupation_canonical, _OCCUPATION_ONLY,
     'enh_normalize_occupation', 'canonical_variant'),
    (clean_remaining_junk, WORK_FIELDS,
     'enh_clean_junk', 'junk_cleanup'),
    (normalize_employer_canonical, _EMPLOYER_ONLY,
     'enh_normalize_employer', 'company_name_formatting'),
    (fix_fullname_in_both_fields, NAME_FIELDS,
     'enh_fix_fullname_both', 'fullname_copied_to_both_fields'),
    (fix_employer_equals_occupation,
     WORK_FIELDS,
     'enh_fix_emp_eq_occ', 'employer_equals_occupation'),
    (apply_employer_synonyms, _EMPLOYER_ONLY,
     'enh_employer_synonyms', 'verified_same_company'),
    (fix_occupation_as_employer, WORK_FIELDS,
     'enh_fix_occ_as_employer', 'occupation_word_in_employer'),
)


# In order: transform, audit name, reason, fields, log message.
_LATE_STEPS = (
    # Fix swapped company names before changing occupation spelling.
    (normalize_occupation_style_step, 'enh_normalize_occupation_style',
     'separator_joiner_or_plural_style_unified', _OCCUPATION_ONLY,
     "Unified occupation style for {n:,} rows (separators, CO-, plurals)"),
    (apply_occupation_typo_fixes, 'enh_occupation_typo_fixes',
     'curated_typo_or_abbreviation_fixed', _OCCUPATION_ONLY,
     "Fixed {n:,} curated occupation typos/abbreviations"),
    (apply_reviewed_employer_names, 'employer_recanonicalize',
     'documented_company_alias', EMPLOYMENT_FIELDS,
     "Applied {n:,} documented company aliases"),
    (_clean_self_employed_variants, 'self_employed_variants',
     'self_employed_variant_unified', WORK_FIELDS,
     "Unified {n:,} self-employed variants -> SELF-EMPLOYED"),
    # Normalization can expose more placeholders.
    (_clean_junk_status_word_employer, 'late_placeholder_employers',
     'refusal_placeholder_employer_nulled', _EMPLOYER_ONLY,
     "Nulled {n:,} refusal/placeholder employers (N/A, PRIVATE, etc.)"),
)


def apply_record_rules(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Apply all record-level rules."""
    log = logger.info

    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.Series(dtype='object', index=df.index)
        trail.run(df, lambda frame: 0, 'previous_employer_column', 'column_created',
                  ('previous_employer',))

    for transform, fields, audit_name, reason in _AUDITED_RULES:
        df, *counts = trail.run(df, transform, audit_name, reason, fields)
        log(f"  {audit_name}: {', '.join(f'{count:,}' for count in counts)}")

    _apply_safety_rules(df, trail, log)
    for transform, audit_name, reason, fields, message in _LATE_STEPS:
        df = trail.run_logged(df, transform, audit_name, reason, fields, message, log)

    return df


def _apply_safety_rules(df: pd.DataFrame, trail: AuditTrail, log: Callable[[str], None]) -> None:
    """Run each safety rule on its declared rows."""
    individuals = df['is_individual'].astype(bool)
    non_individuals = ~individuals
    fixed = 0

    for rule, scope, fields, reason in SAFETY_RULES:
        if scope == ALL_ROWS:
            transform = rule
        elif scope == INDIVIDUALS:
            transform = lambda frame, rule=rule: rule(frame, individuals)
        else:
            transform = lambda frame, rule=rule: rule(frame, non_individuals)
        step = 'safety_' + rule.__name__.lstrip('_')
        fixed += trail.run(df, transform, step, reason, fields)

    if fixed:
        log(f"Safety rules: fixed {fixed:,} remaining inconsistencies")
