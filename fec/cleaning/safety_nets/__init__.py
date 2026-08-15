"""apply_safety_nets() runs every fix in _SAFETY_NETS order; ORDER IS SIGNIFICANT (later nets read values earlier nets wrote or cleared)."""
from __future__ import annotations

import pandas as pd

from fec.log import get_logger

from .committee import (
    _classify_committee_types,
    _fix_committee_employer,
    _fix_individual_committee_type,
    _fix_misclassified_foundation,
    _fix_title_as_first_name,
)
from .occupation import (
    _fix_emp_occ_category_consistency,
    _fix_slash_occupation,
    _null_junk_occupation,
    _reclassify_other_category,
    _fill_null_occupation_category,
    _fix_bitton_edge_case,
    _fix_disclosed_no_employer,
    _fix_employed_as_occupation,
    _fix_employed_no_category,
    _fix_not_disclosed_in_other,
    _fix_not_disclosed_with_real_occ,
    _fix_status_word_in_occupation,
    _fix_web_artifact_occupation,
)
from .employer import (
    _clear_admin_note_employers,
    _clear_orphan_normalized,
    _clear_refusal_employers,
    _fix_email_employer_final,
    _fix_junk_employer_patterns,
    _fix_numeric_employer_final,
    _fix_retired_typos,
    _null_sector_as_employer,
    _fix_truncated_employer_38,
    _fix_choose_prefix,
    _null_short_employer_junk,
)
from .employer_swaps import (
    _fix_employer_equals_occupation,
    _fix_employer_is_occupation_word,
    _fix_occ_emp_both_swapped,
    _fix_role_as_employer,
    _swap_role_employer_with_known_company,
    _fix_swapped_emp_occ_company,
    _fix_company_name_as_occupation,
    _fix_own_name_as_employer,
    _fix_self_employed_consistency,
)
from .addresses import (
    _fill_null_city_from_zip,
    _fix_foreign_addresses,
    _fix_garbage_city_names,
    _fix_pr_zip_wrong_state,
)

logger = get_logger(__name__)

__all__ = ["apply_safety_nets"]

# Row scope each net wants.
_ALL = "all"              # fix(df)
_INDIV = "indiv"          # fix(df, is_individual)
_NON_INDIV = "non_indiv"  # fix(df, ~is_individual)

# (fix function, scope). ORDER IS SIGNIFICANT.
_SAFETY_NETS = [
    # Committee / occupation / employer consistency
    (_fix_committee_employer,           _NON_INDIV),
    (_fix_individual_committee_type,    _INDIV),
    (_classify_committee_types,         _NON_INDIV),
    (_fix_disclosed_no_employer,        _ALL),
    (_fix_employed_no_category,         _ALL),
    (_fix_status_word_in_occupation,    _INDIV),
    (_fix_not_disclosed_with_real_occ,  _INDIV),
    (_fill_null_occupation_category,    _INDIV),
    (_fill_null_city_from_zip,          _ALL),
    (_fix_bitton_edge_case,             _INDIV),
    (_clear_refusal_employers,          _INDIV),
    (_clear_admin_note_employers,       _INDIV),
    (_clear_orphan_normalized,          _ALL),
    (_null_short_employer_junk,         _INDIV),

    # Post-scan fixes from data quality audits
    (_fix_numeric_employer_final,       _INDIV),
    (_fix_email_employer_final,         _INDIV),
    (_fix_junk_employer_patterns,       _INDIV),
    (_fix_retired_typos,                _INDIV),
    (_fix_employer_equals_occupation,   _INDIV),
    (_fix_employer_is_occupation_word,  _INDIV),
    (_fix_misclassified_foundation,     _ALL),
    (_fix_title_as_first_name,          _ALL),
    (_fix_choose_prefix,                _ALL),
    # MUST precede AD: AD collapses a bare title to SELF-EMPLOYED, stranding
    # the real company sitting in the occupation field.
    (_swap_role_employer_with_known_company, _ALL),
    (_fix_role_as_employer,             _ALL),
    (_null_sector_as_employer,          _ALL),
    (_fix_self_employed_consistency,    _ALL),
    (_fix_own_name_as_employer,         _ALL),

    # Address hygiene + truncated-employer repair
    (_fix_foreign_addresses,            _ALL),
    (_fix_garbage_city_names,           _ALL),
    (_fix_pr_zip_wrong_state,           _ALL),
    (_fix_truncated_employer_38,        _ALL),

    # Occupation-field junk: company names, swaps, web artifacts
    (_fix_company_name_as_occupation,   _ALL),
    (_fix_swapped_emp_occ_company,      _ALL),
    (_fix_not_disclosed_in_other,       _ALL),
    (_fix_occ_emp_both_swapped,         _ALL),
    (_fix_web_artifact_occupation,      _ALL),
    (_null_junk_occupation,             _ALL),
    (_fix_employed_as_occupation,       _ALL),

    # Cross-field emp/occ/category consistency
    (_fix_emp_occ_category_consistency, _ALL),
    (_fix_slash_occupation,             _ALL),
    (_reclassify_other_category,        _ALL),
]


def apply_safety_nets(df: pd.DataFrame, verbose: bool = True) -> int:
    """Run every net in _SAFETY_NETS in order; returns total fixes applied."""
    log = logger.info if verbose else lambda _: None

    # computed once: no net mutates entity_type / is_individual
    is_indiv: pd.Series = df['is_individual'].astype(bool)
    not_indiv: pd.Series = ~is_indiv

    n_fixed = 0
    for fix, scope in _SAFETY_NETS:
        if scope is _ALL:
            n_fixed += fix(df)
        elif scope is _INDIV:
            n_fixed += fix(df, is_indiv)
        else:  # _NON_INDIV
            n_fixed += fix(df, not_indiv)

    if n_fixed:
        log(f"Safety net: fixed {n_fixed:,} remaining inconsistencies")
    return n_fixed
