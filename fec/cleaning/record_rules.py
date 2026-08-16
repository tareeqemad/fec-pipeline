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
from fec.cleaning.record_junk import (
    _clean_junk_status_word_employer,
    _clean_self_employed_variants,
    clean_remaining_junk,
)
from fec.cleaning.safety_nets.addresses import (
    _fill_null_city_from_zip,
    _fix_foreign_addresses,
    _fix_garbage_city_names,
    _fix_pr_zip_wrong_state,
)
from fec.cleaning.safety_nets.committee import (
    _classify_committee_types,
    _fix_committee_employer,
    _fix_individual_committee_type,
    _fix_misclassified_foundation,
    _fix_title_as_first_name,
)
from fec.cleaning.safety_nets.employer import (
    _clear_admin_note_employers,
    _clear_orphan_normalized,
    _clear_refusal_employers,
    _fix_choose_prefix,
    _fix_email_employer_final,
    _fix_junk_employer_patterns,
    _fix_numeric_employer_final,
    _fix_retired_typos,
    _fix_truncated_employer_38,
    _null_sector_as_employer,
    _null_short_employer_junk,
)
from fec.cleaning.safety_nets.employer_swaps import (
    _fix_company_name_as_occupation,
    _fix_employer_equals_occupation,
    _fix_employer_is_occupation_word,
    _fix_occ_emp_both_swapped,
    _fix_own_name_as_employer,
    _fix_role_as_employer,
    _fix_self_employed_consistency,
    _fix_swapped_emp_occ_company,
    _swap_role_employer_with_known_company,
)
from fec.cleaning.safety_nets.occupation import (
    _fill_null_occupation_category,
    _fix_bitton_edge_case,
    _fix_disclosed_no_employer,
    _fix_emp_occ_category_consistency,
    _fix_employed_as_occupation,
    _fix_employed_no_category,
    _fix_not_disclosed_in_other,
    _fix_not_disclosed_with_real_occ,
    _fix_slash_occupation,
    _fix_status_word_in_occupation,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
    _reclassify_other_category,
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


_CLASSIFICATION_FIELDS = (
    'entity_type', 'is_individual', 'contributor_first_name',
    'contributor_last_name', 'occupation_category', 'occupation_status',
    'contributor_occupation', 'committee_type',
)
_BUSINESS_CLASSIFICATION_FIELDS = _CLASSIFICATION_FIELDS[:-1]
_PERSON_NAME_FIELDS = (
    'contributor_name', 'contributor_first_name', 'contributor_last_name',
)
_EMPLOYMENT_FIELDS = (
    'contributor_occupation', 'contributor_employer',
    'occupation_category', 'occupation_status',
)
_NAME_ONLY = ('contributor_name',)
_OCCUPATION_ONLY = ('contributor_occupation',)
_EMPLOYER_ONLY = ('contributor_employer',)
_NORMALIZED_EMPLOYER_ONLY = ('employer_name_normalized',)

# Field-level rules in execution order. The order is part of the data contract.
_AUDITED_RULES = (
    _Step(fix_remaining_misclassified, _CLASSIFICATION_FIELDS,
          'enh_reclassify_committee', 'committee_name_pattern'),
    _Step(fix_misclassified_business_entities, _BUSINESS_CLASSIFICATION_FIELDS,
          'enh_reclassify_business', 'business_suffix_or_org_pattern'),
    _Step(normalize_business_names, _NAME_ONLY,
          'enh_normalize_business_names', 'strip_legal_suffix'),
    _Step(normalize_name_periods, _NAME_ONLY,
          'enh_normalize_name_periods', 'remove_periods_from_initials'),
    _Step(fix_credential_in_name, _PERSON_NAME_FIELDS,
          'enh_fix_credential_in_name', 'strip_credential_between_last_first'),
    _Step(apply_name_corrections, _NAME_ONLY,
          'enh_name_corrections', 'manual_correction'),
    _Step(fix_double_apostrophes, _PERSON_NAME_FIELDS,
          'enh_fix_apostrophes', 'double_apostrophe_cleanup'),
    _Step(fix_remaining_swapped_occ_emp, _EMPLOYMENT_FIELDS,
          'enh_swap_occ_emp', 'occ_emp_swap_or_same_value'),
    _Step(normalize_occupation_canonical, _OCCUPATION_ONLY,
          'enh_normalize_occupation', 'canonical_variant'),
    _Step(clean_remaining_junk, _EMPLOYMENT_FIELDS,
          'enh_clean_junk', 'junk_cleanup'),
    _Step(normalize_employer_canonical, _NORMALIZED_EMPLOYER_ONLY,
          'enh_normalize_employer', 'strip_suffix_normalize_and'),
    _Step(fix_fullname_in_both_fields, _PERSON_NAME_FIELDS,
          'enh_fix_fullname_both', 'fullname_copied_to_both_fields'),
    _Step(fix_employer_equals_occupation,
          ('contributor_occupation', 'occupation_category', 'occupation_status'),
          'enh_fix_emp_eq_occ', 'employer_equals_occupation'),
    _Step(fix_normalized_mid_suffix, _NORMALIZED_EMPLOYER_ONLY,
          'enh_fix_norm_mid_suffix', 'strip_mid_string_llc_inc'),
    _Step(apply_employer_synonyms, _EMPLOYER_ONLY,
          'enh_employer_synonyms', 'verified_same_company'),
    _Step(fix_occupation_as_employer, _EMPLOYMENT_FIELDS,
          'enh_fix_occ_as_employer', 'occupation_word_in_employer'),
)


ALL_ROWS = "all"
INDIVIDUALS = "individuals"
NON_INDIVIDUALS = "non_individuals"

# Residual rules run after the audited record rules above.
SAFETY_RULES = (
    (_fix_committee_employer, NON_INDIVIDUALS),
    (_fix_individual_committee_type, INDIVIDUALS),
    (_classify_committee_types, NON_INDIVIDUALS),
    (_fix_disclosed_no_employer, ALL_ROWS),
    (_fix_employed_no_category, ALL_ROWS),
    (_fix_status_word_in_occupation, INDIVIDUALS),
    (_fix_not_disclosed_with_real_occ, INDIVIDUALS),
    (_fill_null_occupation_category, INDIVIDUALS),
    (_fill_null_city_from_zip, ALL_ROWS),
    (_fix_bitton_edge_case, INDIVIDUALS),
    (_clear_refusal_employers, INDIVIDUALS),
    (_clear_admin_note_employers, INDIVIDUALS),
    (_clear_orphan_normalized, ALL_ROWS),
    (_null_short_employer_junk, INDIVIDUALS),
    (_fix_numeric_employer_final, INDIVIDUALS),
    (_fix_email_employer_final, INDIVIDUALS),
    (_fix_junk_employer_patterns, INDIVIDUALS),
    (_fix_retired_typos, INDIVIDUALS),
    (_fix_employer_equals_occupation, INDIVIDUALS),
    (_fix_employer_is_occupation_word, INDIVIDUALS),
    (_fix_misclassified_foundation, ALL_ROWS),
    (_fix_title_as_first_name, ALL_ROWS),
    (_fix_choose_prefix, ALL_ROWS),
    # Preserve the real company before a bare role becomes SELF-EMPLOYED.
    (_swap_role_employer_with_known_company, ALL_ROWS),
    (_fix_role_as_employer, ALL_ROWS),
    (_null_sector_as_employer, ALL_ROWS),
    (_fix_self_employed_consistency, ALL_ROWS),
    (_fix_own_name_as_employer, ALL_ROWS),
    (_fix_foreign_addresses, ALL_ROWS),
    (_fix_garbage_city_names, ALL_ROWS),
    (_fix_pr_zip_wrong_state, ALL_ROWS),
    (_fix_truncated_employer_38, ALL_ROWS),
    (_fix_company_name_as_occupation, ALL_ROWS),
    (_fix_swapped_emp_occ_company, ALL_ROWS),
    (_fix_not_disclosed_in_other, ALL_ROWS),
    (_fix_occ_emp_both_swapped, ALL_ROWS),
    (_fix_web_artifact_occupation, ALL_ROWS),
    (_null_junk_occupation, ALL_ROWS),
    (_fix_employed_as_occupation, ALL_ROWS),
    (_fix_emp_occ_category_consistency, ALL_ROWS),
    (_fix_slash_occupation, ALL_ROWS),
    (_reclassify_other_category, ALL_ROWS),
)


def apply_record_rules(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Apply all record-level rules and return their audit."""
    log = logger.info
    audit_records: list[dict] = []

    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')

    for step in _AUDITED_RULES:
        df = _apply_step(df, audit_records, log, step)

    _apply_safety_rules(df, log)
    n_recanon = _recanonicalize_employers(df)
    if n_recanon:
        log(f"Re-canonicalized {n_recanon:,} employer variants")
    n_restored = restore_display_suffixes(df, RAW_CSV)
    if n_restored:
        log(f"Restored display suffixes for {n_restored:,} rows (HOUSING -> HOUSING INC etc.)")

    # re-assert curated synonyms: the two passes above can revert one to a raw form
    before = {'contributor_employer': df['contributor_employer'].copy()}
    df, n_synonyms_final = apply_employer_synonyms(df)
    _append_changes(
        df, before, audit_records,
        'enh_employer_synonyms_final', 'verified_same_company',
    )
    if n_synonyms_final:
        log(f"Re-applied {n_synonyms_final:,} employer synonyms (final pass)")

    # abbreviation expansion AFTER synonyms, so forms a synonym target reintroduces also collapse
    df, n_abbr = expand_employer_abbreviations(df)
    if n_abbr:
        log(f"Expanded {n_abbr:,} employer abbreviations (MGMT->MANAGEMENT, ...)")
    # ASSOC is contextual: real associations -> ASSOCIATION, the rest -> ASSOCIATES
    df, n_assoc = expand_employer_associates(df)
    if n_assoc:
        log(f"Normalized {n_assoc:,} ASSOC employers (-> ASSOCIATES / ASSOCIATION)")
    n_self = _clean_self_employed_variants(df)
    if n_self:
        log(f"Unified {n_self:,} self-employed variants -> SELF-EMPLOYED")

    # final sweep: refusals/placeholders that only became skip-words after the normalization above
    n_null_emp = _clean_junk_status_word_employer(df)
    if n_null_emp:
        log(f"Nulled {n_null_emp:,} refusal/placeholder employers (N/A, PRIVATE, etc.)")
    df.drop(columns=['employer_name_normalized'], inplace=True)
    log("  -> Dropped employer_name_normalized (merged into contributor_employer)")
    log(f"  -> Record audit: {len(audit_records):,} field changes tracked")
    return df, audit_records


def _apply_safety_rules(df: pd.DataFrame, log: Callable[[str], None]) -> int:
    individuals = df['is_individual'].astype(bool)
    non_individuals = ~individuals
    fixed = 0

    for rule, scope in SAFETY_RULES:
        if scope == ALL_ROWS:
            fixed += rule(df)
        elif scope == INDIVIDUALS:
            fixed += rule(df, individuals)
        else:
            fixed += rule(df, non_individuals)

    if fixed:
        log(f"Safety rules: fixed {fixed:,} remaining inconsistencies")
    return fixed


def _apply_step(
    df: pd.DataFrame,
    audit_records: list[dict],
    log: Callable[[str], None],
    step: _Step,
) -> pd.DataFrame:
    """Apply one declared step and record the fields it owns."""
    before = {
        field: df[field].copy()
        for field in step.fields
        if field in df.columns
    }
    result = step.transform(df)
    df, *counts = result
    _append_changes(df, before, audit_records, step.audit_name, step.reason)
    log(f"  {step.audit_name}: {', '.join(f'{count:,}' for count in counts)}")
    return df


def _append_changes(
    df: pd.DataFrame,
    before: dict[str, pd.Series],
    audit_records: list[dict],
    step: str,
    reason: str,
) -> None:
    for field, before_series in before.items():
        after_series = df[field]
        before_text = before_series.fillna('').astype(str)
        after_text = after_series.fillna('').astype(str)
        changed = before_text != after_text
        for idx in df.index[changed]:
            audit_records.append({
                'sub_id': str(df.at[idx, 'sub_id']),
                'field': field,
                'before': before_text.at[idx],
                'after': after_text.at[idx],
                'step': step,
                'reason': reason,
            })
