"""Master sequence for the post-cleaning enhancements."""
import re
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from fec.cleaning.entity_classification import (
    fix_remaining_misclassified, fix_misclassified_business_entities, normalize_business_names,
    apply_name_corrections, fix_double_apostrophes, normalize_name_periods,
    fix_credential_in_name, fix_fullname_in_both_fields, fix_employer_equals_occupation,
)
from fec.cleaning.employer_synonyms import (
    normalize_employer_canonical, apply_employer_synonyms, expand_employer_abbreviations,
    expand_employer_associates, fix_occupation_as_employer, fix_normalized_mid_suffix,
    _recanonicalize_employers, restore_display_suffixes,
)
from fec.cleaning._helpers import _indiv_idx, _norm
from fec.cleaning.occupations import _categorize
from fec.cleaning.safety_nets import apply_safety_nets
from fec.cleaning.enhancements.junk import (clean_remaining_junk,
                                             _clean_junk_status_word_employer,
                                             _clean_self_employed_variants)
from fec.config.occupation_rules.rules import OCCUPATION_CANONICAL, OCCUPATION_KEYWORDS
from fec.env import RAW_CSV
from fec.log import get_logger

logger = get_logger(__name__)

_CORP_IN_OCC_RE = re.compile(
    r'\bLLC\b|\bLLP\b|\bINC\b\.?|\bCORP\b|\bLTD\b'
    r'|\bCOMPANY\b|\bCORPORATION\b|\bHOLDINGS\b|\bGROUP\b'
    r'|\bPARTNERS\b|\bVENTURES\b|\bCAPITAL\b|\bFUND\b'
    r'|\bASSOCIATES\b|\bENTERPRISES\b|\bPROPERTIES\b|\bREALTY\b'
    r'|\bADVISORS\b|\bINSURANCE\b|\bINDUSTRIES\b|\bBROTHERS\b'
    r'|\bBANK\b|\bFINANCIAL\b|\bMEDIA\b|\bSYSTEMS\b'
    r'|\bTECHNOLOGIES\b|\bSOLUTIONS\b|\bSERVICES\b|\bMANAGEMENT\b'
    r'|\bTRUST\b|\bINTERNATIONAL\b|\bGLOBAL\b'
    r'|\b\w+\s*&\s*\w+\b'
    r'|& (?:PARTNERS|ASSOCIATES|CRUTCHER|DE LLANO|BUTLER)',
)
_ORG_IN_OCC_RE = re.compile(
    r'\bHOSPITAL\b|\bUNIVERSITY\b|\bINSTITUTE\b'
    r'|\bCOLLEGE\b|\bSCHOOL\b|\bACADEMY\b'
    r'|\bFOUNDATION\b|\bAGENCY\b|\bBUREAU\b'
    r'|\bDEPARTMENT\b|\bMINISTRY\b|\bAIPAC\b|\bDMFI\b',
)


def _swap_occ_emp(df: pd.DataFrame, indexes: pd.Index) -> None:
    """Swap occupation and employer, then update the category."""
    old_occupation = df.loc[indexes, 'contributor_occupation'].copy()
    old_employer = df.loc[indexes, 'contributor_employer'].copy()
    df.loc[indexes, 'contributor_occupation'] = old_employer
    df.loc[indexes, 'contributor_employer'] = old_occupation
    df.loc[indexes, 'occupation_category'] = _categorize(
        df.loc[indexes, 'contributor_occupation']
    )


def fix_remaining_swapped_occ_emp(df: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Fix remaining occupation/employer swaps."""
    individuals = _indiv_idx(df)
    occupation = _norm(df.loc[individuals, 'contributor_occupation'])
    employer = _norm(df.loc[individuals, 'contributor_employer'])

    employer_is_job = employer.isin(OCCUPATION_KEYWORDS)
    occupation_is_company = occupation.str.contains(_CORP_IN_OCC_RE, na=False)
    occupation_starts_corp = occupation.str.startswith('CORP ', na=False)
    company_swaps = individuals[
        occupation_is_company & employer_is_job & ~occupation_starts_corp
    ]
    if len(company_swaps):
        _swap_occ_emp(df, company_swaps)

    same = individuals[(occupation == employer) & employer_is_job]
    if len(same):
        df.loc[same, 'contributor_employer'] = 'SELF-EMPLOYED'

    organization_swaps = individuals[
        occupation.str.contains(_ORG_IN_OCC_RE, na=False) & employer_is_job
    ]
    if len(organization_swaps):
        _swap_occ_emp(df, organization_swaps)

    return df, len(company_swaps) + len(organization_swaps), len(same)


def normalize_occupation_canonical(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Map safe occupation variants to their canonical form."""
    individuals = _indiv_idx(df)
    occupation = df.loc[individuals, 'contributor_occupation']
    hits = individuals[occupation.isin(OCCUPATION_CANONICAL)]
    if len(hits):
        df.loc[hits, 'contributor_occupation'] = occupation[hits].map(
            OCCUPATION_CANONICAL
        )
    return df, len(hits)


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
_AUDITED_STEPS = (
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


def run_enhancements(
    df: pd.DataFrame, verbose: bool = True,
) -> tuple[pd.DataFrame, list[dict]]:
    """Run all post-cleaning enhancements and return the data plus its audit."""
    log = logger.info if verbose else lambda _: None
    audit_records: list[dict] = []

    df['is_individual'] = (df['entity_type'] == 'INDIVIDUAL')

    for step in _AUDITED_STEPS:
        df = _apply_step(df, audit_records, log, step)

    apply_safety_nets(df, verbose=verbose)
    n_recanon = _recanonicalize_employers(df)
    if n_recanon:
        log(f"Re-canonicalized {n_recanon:,} employer variants (post-enhancement)")
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
    log(f"  -> Enhancement audit: {len(audit_records):,} field changes tracked")
    return df, audit_records


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
