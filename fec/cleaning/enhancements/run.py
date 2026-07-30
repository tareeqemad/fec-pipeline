"""Master sequence for the post-cleaning enhancements."""
import pandas as pd

from fec.cleaning.entity_classification import (
    fix_remaining_misclassified, fix_misclassified_business_entities, normalize_business_names,
    apply_name_corrections, fix_double_apostrophes, fix_null_last_name, normalize_name_periods,
    fix_credential_in_name, fix_fullname_in_both_fields, fix_employer_equals_occupation,
)
from fec.cleaning.employer_synonyms import (
    normalize_employer_canonical, apply_employer_synonyms, expand_employer_abbreviations,
    expand_employer_associates, fix_occupation_as_employer, fix_normalized_mid_suffix,
    _recanonicalize_employers, restore_display_suffixes, merge_typo_variants,
)
from fec.cleaning.safety_nets import apply_safety_nets
from fec.cleaning.enhancements.swaps import (fix_remaining_swapped_occ_emp,
                                             normalize_occupation_canonical)
from fec.cleaning.enhancements.junk import (clean_remaining_junk,
                                            _clean_junk_status_word_employer,
                                            _clean_self_employed_variants)
from fec.env import RAW_CSV
from fec.log import get_logger

logger = get_logger(__name__)


def run_enhancements(
    df: pd.DataFrame, verbose: bool = True,
) -> tuple[pd.DataFrame, dict[str, int], list[dict]]:
    """Run all post-cleaning enhancements. Returns: (df, report_dict, audit_records)"""
    log = logger.info if verbose else lambda msg: None
    report: dict[str, int] = {}
    audit_records: list[dict] = []

    if 'is_individual' not in df.columns:
        if 'entity_type' in df.columns:
            df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')
        else:
            df['is_individual'] = True

    snapshot = _snap(df, ['entity_type', 'is_individual', 'contributor_first_name', 'contributor_last_name',
                   'occupation_category', 'occupation_status', 'contributor_occupation', 'committee_type'])
    df, n_reclassified = fix_remaining_misclassified(df)
    _diff(df, snapshot, audit_records, 'enh_reclassify_committee', 'committee_name_pattern')
    report['reclassified_to_committee'] = n_reclassified
    log(f"Reclassified {n_reclassified:,} remaining individuals -> COMMITTEE/PAC")
    snapshot = _snap(df, ['entity_type', 'is_individual', 'contributor_first_name', 'contributor_last_name',
                   'occupation_category', 'occupation_status', 'contributor_occupation'])
    df, n_business = fix_misclassified_business_entities(df)
    _diff(df, snapshot, audit_records, 'enh_reclassify_business', 'business_suffix_or_org_pattern')
    report['reclassified_business_entities'] = n_business
    log(f"Reclassified {n_business:,} business entities -> COMMITTEE/PAC")

    snapshot = _snap(df, ['contributor_name'])
    df, n_business_names = normalize_business_names(df)
    _diff(df, snapshot, audit_records, 'enh_normalize_business_names', 'strip_legal_suffix')
    report['normalized_business_names'] = n_business_names
    log(f"Normalized {n_business_names:,} business names (stripped LLC/LLP/INC etc.)")

    snapshot = _snap(df, ['contributor_name'])
    df, n_periods = normalize_name_periods(df)
    _diff(df, snapshot, audit_records, 'enh_normalize_name_periods', 'remove_periods_from_initials')
    report['normalized_name_periods'] = n_periods
    log(f"Normalized {n_periods:,} name periods (I.R. -> I R, MR. -> MR)")
    snapshot = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, n_credentials = fix_credential_in_name(df)
    _diff(df, snapshot, audit_records, 'enh_fix_credential_in_name', 'strip_credential_between_last_first')
    report['credential_in_name_fixed'] = n_credentials
    log(f"Stripped credentials from {n_credentials:,} names (M.D./Ph.D./J.D. wedged in LAST, X, FIRST)")
    snapshot = _snap(df, ['contributor_name'])
    df, n_corrections = apply_name_corrections(df)
    _diff(df, snapshot, audit_records, 'enh_name_corrections', 'manual_correction')
    report['name_corrections'] = n_corrections
    log(f"Applied {n_corrections:,} manual name corrections")
    snapshot = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, _ = fix_double_apostrophes(df)
    _diff(df, snapshot, audit_records, 'enh_fix_apostrophes', 'double_apostrophe_cleanup')
    snapshot = _snap(df, ['contributor_name', 'contributor_last_name'])
    df, n_null_lastname = fix_null_last_name(df)
    _diff(df, snapshot, audit_records, 'enh_fix_null_lastname', 'literal_null_in_lastname')
    report['null_lastname_fixed'] = n_null_lastname
    log(f"Fixed {n_null_lastname:,} records with literal NULL as last name")

    snapshot = _snap(df, ['contributor_occupation', 'contributor_employer', 'occupation_category', 'occupation_status'])
    df, n_swap, n_same = fix_remaining_swapped_occ_emp(df)
    _diff(df, snapshot, audit_records, 'enh_swap_occ_emp', 'occ_emp_swap_or_same_value')
    report['occ_emp_swapped'] = n_swap
    report['occ_emp_same_fixed'] = n_same
    log(f"Swapped {n_swap:,} occ<->emp, fixed {n_same:,} same-value pairs")
    snapshot = _snap(df, ['contributor_occupation'])
    df, n_occ_canonical = normalize_occupation_canonical(df)
    _diff(df, snapshot, audit_records, 'enh_normalize_occupation', 'canonical_variant')
    report['occupation_normalized'] = n_occ_canonical
    log(f"Normalized {n_occ_canonical:,} occupation variants -> canonical")
    snapshot = _snap(df, ['contributor_occupation', 'contributor_employer', 'occupation_category', 'occupation_status'])
    df, n_junk = clean_remaining_junk(df)
    _diff(df, snapshot, audit_records, 'enh_clean_junk', 'junk_cleanup')
    report['junk_cleaned'] = n_junk
    log(f"Cleaned {n_junk:,} remaining junk values")

    snapshot = _snap(df, ['employer_name_normalized'])
    df, n_emp_norm = normalize_employer_canonical(df)
    _diff(df, snapshot, audit_records, 'enh_normalize_employer', 'strip_suffix_normalize_and')
    report['employer_normalized'] = n_emp_norm
    log(f"Normalized {n_emp_norm:,} employer name variants")

    snapshot = _snap(df, ['contributor_name', 'contributor_first_name', 'contributor_last_name'])
    df, n_fullname = fix_fullname_in_both_fields(df)
    _diff(df, snapshot, audit_records, 'enh_fix_fullname_both', 'fullname_copied_to_both_fields')
    report['fullname_both_fixed'] = n_fullname
    log(f"Fixed {n_fullname:,} records with full name in both first & last")
    snapshot = _snap(df, ['contributor_occupation', 'occupation_category', 'occupation_status'])
    df, n_emp_eq_occ = fix_employer_equals_occupation(df)
    _diff(df, snapshot, audit_records, 'enh_fix_emp_eq_occ', 'employer_equals_occupation')
    report['emp_eq_occ_fixed'] = n_emp_eq_occ
    log(f"Fixed {n_emp_eq_occ:,} records where employer = occupation")

    snapshot = _snap(df, ['employer_name_normalized'])
    df, n_mid_suffix = fix_normalized_mid_suffix(df)
    _diff(df, snapshot, audit_records, 'enh_fix_norm_mid_suffix', 'strip_mid_string_llc_inc')
    report['norm_mid_suffix_fixed'] = n_mid_suffix
    log(f"Fixed {n_mid_suffix:,} normalized employers with mid-string LLC/INC")
    snapshot = _snap(df, ['contributor_employer'])
    df, n_synonyms = apply_employer_synonyms(df)
    _diff(df, snapshot, audit_records, 'enh_employer_synonyms', 'verified_same_company')
    report['employer_synonyms_merged'] = n_synonyms
    log(f"Merged {n_synonyms:,} employer name variants -> canonical")
    snapshot = _snap(df, ['contributor_employer', 'contributor_occupation', 'occupation_category', 'occupation_status'])
    df, n_occ_as_emp = fix_occupation_as_employer(df)
    _diff(df, snapshot, audit_records, 'enh_fix_occ_as_employer', 'occupation_word_in_employer')
    report['occ_as_employer_fixed'] = n_occ_as_emp
    log(f"Fixed {n_occ_as_emp:,} records where employer was an occupation word")

    apply_safety_nets(df, verbose=verbose)
    n_recanon = _recanonicalize_employers(df)
    if n_recanon:
        report['employer_recanonicalized'] = n_recanon
        log(f"Re-canonicalized {n_recanon:,} employer variants (post-enhancement)")
    n_restored = restore_display_suffixes(df, RAW_CSV)
    if n_restored:
        report['display_suffixes_restored'] = n_restored
        log(f"Restored display suffixes for {n_restored:,} rows (HOUSING -> HOUSING INC etc.)")

    # fuzzy typo merging runs LAST to catch typos the earlier passes leave; covers previous_employer too
    n_typo = merge_typo_variants(df)
    if n_typo:
        report['typo_variants_merged'] = n_typo
        log(f"Merged {n_typo:,} typo-variant rows (fuzzy match >= 92%)")

    # re-assert curated synonyms: the two passes above can revert one to a raw form
    snapshot = _snap(df, ['contributor_employer'])
    df, n_synonyms_final = apply_employer_synonyms(df)
    _diff(df, snapshot, audit_records, 'enh_employer_synonyms_final', 'verified_same_company')
    if n_synonyms_final:
        report['employer_synonyms_merged'] = report.get('employer_synonyms_merged', 0) + n_synonyms_final
        log(f"Re-applied {n_synonyms_final:,} employer synonyms (final pass)")

    # abbreviation expansion AFTER synonyms, so forms a synonym target reintroduces also collapse
    df, n_abbr = expand_employer_abbreviations(df)
    if n_abbr:
        report['employer_abbreviations_expanded'] = n_abbr
        log(f"Expanded {n_abbr:,} employer abbreviations (MGMT->MANAGEMENT, ...)")
    # ASSOC is contextual: real associations -> ASSOCIATION, the rest -> ASSOCIATES
    df, n_assoc = expand_employer_associates(df)
    if n_assoc:
        report['employer_associates_normalized'] = n_assoc
        log(f"Normalized {n_assoc:,} ASSOC employers (-> ASSOCIATES / ASSOCIATION)")
    n_self = _clean_self_employed_variants(df)
    if n_self:
        report['self_employed_unified'] = n_self
        log(f"Unified {n_self:,} self-employed variants -> SELF-EMPLOYED")

    # final sweep: refusals/placeholders that only became skip-words after the normalization above
    n_null_emp = _clean_junk_status_word_employer(df)
    if n_null_emp:
        report['status_word_employer_nulled'] = n_null_emp
        log(f"Nulled {n_null_emp:,} refusal/placeholder employers (N/A, PRIVATE, etc.)")
    if 'employer_name_normalized' in df.columns:
        df.drop(columns=['employer_name_normalized'], inplace=True)
        log("  -> Dropped employer_name_normalized (merged into contributor_employer)")
    log(f"  -> Enhancement audit: {len(audit_records):,} field changes tracked")
    return df, report, audit_records


def _snap(df: pd.DataFrame, cols: list[str]) -> dict[str, pd.Series]:
    if 'sub_id' not in df.columns:
        return {}
    return {col: df[col].copy() for col in cols if col in df.columns}


def _diff(df: pd.DataFrame, snap: dict[str, pd.Series],
          audit_records: list[dict], step: str, reason: str) -> None:
    if 'sub_id' not in df.columns:
        return
    for col, before_series in snap.items():
        after_series = df[col]
        before_text = before_series.fillna('').astype(str)
        after_text = after_series.fillna('').astype(str)
        changed = before_text != after_text
        for idx in df.index[changed]:
            audit_records.append({
                'sub_id': str(df.at[idx, 'sub_id']),
                'field': col,
                'before': before_text.at[idx],
                'after': after_text.at[idx],
                'step': step,
                'reason': reason,
            })
