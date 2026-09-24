"""Run record-level cleaning rules in one declared order."""
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from fec.cleaning.entity_classification import (
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
from fec.cleaning.employer_synonyms import (
    _recanonicalize_employers,
    apply_employer_synonyms,
    expand_employer_abbreviations,
    expand_employer_associates,
    fix_normalized_mid_suffix,
    fix_occupation_as_employer,
    normalize_employer_canonical,
    restore_display_suffixes,
)
from fec.cleaning.occupations.clean import (
    fix_remaining_swapped_occ_emp,
    normalize_occupation_canonical,
)
from fec.cleaning.occupations.style import (
    apply_occupation_typo_fixes,
    normalize_occupation_style_step,
)
from fec.cleaning.record_junk import (
    _clean_junk_status_word_employer,
    _clean_self_employed_variants,
    clean_remaining_junk,
)
from fec.cleaning.audit_trail import (
    EMPLOYMENT_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.safety_nets import (
    ALL_ROWS,
    INDIVIDUALS,
    SAFETY_RULES,
)
from fec.env import RAW_CSV
from fec.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Step:
    transform: Callable[[pd.DataFrame], tuple]
    fields: tuple[str, ...]
    audit_name: str
    reason: str


_CLASSIFICATION_FIELDS = ENTITY_FIELDS + (
    'contributor_first_name', 'contributor_last_name',
    'occupation_category', 'contributor_occupation',
)
_NAME_ONLY = ('contributor_name',)
_OCCUPATION_ONLY = ('contributor_occupation',)
_EMPLOYER_ONLY = ('contributor_employer',)
_NORMALIZED_EMPLOYER_ONLY = ('employer_name_normalized',)

# Field-level rules in execution order. The order is part of the data contract.
_AUDITED_RULES = (
    _Step(fix_remaining_misclassified, _CLASSIFICATION_FIELDS,
          'enh_reclassify_committee', 'committee_name_pattern'),
    _Step(fix_misclassified_business_entities, _CLASSIFICATION_FIELDS,
          'enh_reclassify_business', 'business_suffix_or_org_pattern'),
    _Step(normalize_business_names, _NAME_ONLY,
          'enh_normalize_business_names', 'strip_legal_suffix'),
    _Step(normalize_name_periods, _NAME_ONLY,
          'enh_normalize_name_periods', 'remove_periods_from_initials'),
    _Step(fix_credential_in_name, NAME_FIELDS,
          'enh_fix_credential_in_name', 'strip_credential_between_last_first'),
    _Step(apply_name_corrections, NAME_FIELDS,
          'enh_name_corrections', 'manual_correction'),
    _Step(fix_double_apostrophes, NAME_FIELDS,
          'enh_fix_apostrophes', 'double_apostrophe_cleanup'),
    _Step(fix_remaining_swapped_occ_emp, WORK_FIELDS,
          'enh_swap_occ_emp', 'occ_emp_swap_or_same_value'),
    _Step(normalize_occupation_canonical, _OCCUPATION_ONLY,
          'enh_normalize_occupation', 'canonical_variant'),
    _Step(clean_remaining_junk, WORK_FIELDS,
          'enh_clean_junk', 'junk_cleanup'),
    _Step(normalize_employer_canonical, _NORMALIZED_EMPLOYER_ONLY + _EMPLOYER_ONLY,
          'enh_normalize_employer', 'strip_suffix_normalize_and'),
    _Step(fix_fullname_in_both_fields, NAME_FIELDS,
          'enh_fix_fullname_both', 'fullname_copied_to_both_fields'),
    _Step(fix_employer_equals_occupation,
          ('contributor_occupation', 'occupation_category'),
          'enh_fix_emp_eq_occ', 'employer_equals_occupation'),
    _Step(fix_normalized_mid_suffix, _NORMALIZED_EMPLOYER_ONLY + _EMPLOYER_ONLY,
          'enh_fix_norm_mid_suffix', 'strip_mid_string_llc_inc'),
    _Step(apply_employer_synonyms, _EMPLOYER_ONLY,
          'enh_employer_synonyms', 'verified_same_company'),
    _Step(fix_occupation_as_employer, WORK_FIELDS,
          'enh_fix_occ_as_employer', 'occupation_word_in_employer'),
)


def apply_record_rules(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Apply all record-level rules."""
    log = logger.info

    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.Series(dtype='object', index=df.index)
        trail.run(df, lambda frame: 0, 'previous_employer_column', 'column_created',
                  ('previous_employer',))

    for step in _AUDITED_RULES:
        df = _apply_step(df, trail, log, step)

    _apply_safety_rules(df, trail, log)
    # style unification runs only after every swap safety net has moved company
    # names out of the occupation column (a de-pluralized company name would no
    # longer match the known-employer set those nets rely on)
    df, n_style = trail.run(
        df, normalize_occupation_style_step, 'enh_normalize_occupation_style',
        'separator_joiner_or_plural_style_unified', _OCCUPATION_ONLY,
    )
    if n_style:
        log(f"Unified occupation style for {n_style:,} rows (separators, CO-, plurals)")
    df, n_typo = trail.run(
        df, apply_occupation_typo_fixes, 'enh_occupation_typo_fixes',
        'curated_typo_or_abbreviation_fixed', _OCCUPATION_ONLY,
    )
    if n_typo:
        log(f"Fixed {n_typo:,} curated occupation typos/abbreviations")
    n_recanon = trail.run(
        df, _recanonicalize_employers, 'employer_recanonicalize',
        'employer_variant_unified_by_canonical_key', EMPLOYMENT_FIELDS,
    )
    if n_recanon:
        log(f"Re-canonicalized {n_recanon:,} employer variants")
    n_restored = trail.run(
        df, lambda frame: restore_display_suffixes(frame, RAW_CSV), 'employer_restore_suffixes',
        'employer_display_form_restored_from_raw', WORK_FIELDS,
    )
    if n_restored:
        log(f"Restored display suffixes for {n_restored:,} rows (HOUSING -> HOUSING INC etc.)")

    # re-assert curated synonyms: the two passes above can revert one to a raw form
    df, n_synonyms_final = trail.run(
        df, apply_employer_synonyms, 'enh_employer_synonyms_final', 'verified_same_company',
        _EMPLOYER_ONLY,
    )
    if n_synonyms_final:
        log(f"Re-applied {n_synonyms_final:,} employer synonyms (final pass)")

    # abbreviation expansion AFTER synonyms, so forms a synonym target reintroduces also collapse
    df, n_abbr = trail.run(
        df, expand_employer_abbreviations, 'employer_expand_abbreviations',
        'employer_abbreviation_expanded', _EMPLOYER_ONLY,
    )
    if n_abbr:
        log(f"Expanded {n_abbr:,} employer abbreviations (MGMT->MANAGEMENT, ...)")
    # ASSOC is contextual: real associations -> ASSOCIATION, the rest -> ASSOCIATES
    df, n_assoc = trail.run(
        df, expand_employer_associates, 'employer_expand_assoc',
        'employer_assoc_expanded', _EMPLOYER_ONLY,
    )
    if n_assoc:
        log(f"Normalized {n_assoc:,} ASSOC employers (-> ASSOCIATES / ASSOCIATION)")
    n_self = trail.run(
        df, _clean_self_employed_variants, 'self_employed_variants',
        'self_employed_variant_unified', WORK_FIELDS,
    )
    if n_self:
        log(f"Unified {n_self:,} self-employed variants -> SELF-EMPLOYED")

    # final sweep: refusals/placeholders that only became skip-words after the normalization above
    n_null_emp = trail.run(
        df, _clean_junk_status_word_employer, 'late_placeholder_employers',
        'refusal_placeholder_employer_nulled', _EMPLOYER_ONLY,
    )
    if n_null_emp:
        log(f"Nulled {n_null_emp:,} refusal/placeholder employers (N/A, PRIVATE, etc.)")
    df.drop(columns=['employer_name_normalized'], inplace=True)
    log("  -> Dropped employer_name_normalized (merged into contributor_employer)")
    return df


def _apply_safety_rules(df: pd.DataFrame, trail: AuditTrail, log: Callable[[str], None]) -> None:
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


def _apply_step(
    df: pd.DataFrame,
    trail: AuditTrail,
    log: Callable[[str], None],
    step: _Step,
) -> pd.DataFrame:
    """Apply one declared step and record the fields it owns."""
    result = trail.run(df, step.transform, step.audit_name, step.reason, step.fields)
    df, *counts = result
    log(f"  {step.audit_name}: {', '.join(f'{count:,}' for count in counts)}")
    return df
