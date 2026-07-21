"""cleaning/safety_nets — final consistency fixes for the enhancement pipeline.

apply_safety_nets() runs every fix in the order listed in ``_SAFETY_NETS``;
the fixes themselves live in focused sibling modules (committee / occupation /
employer / names / addresses). Each fix is independent: it takes the frame
(optionally with a row mask) and returns the count of rows it changed.

To add or remove a safety net, edit the ``_SAFETY_NETS`` table below — one
entry per step. ORDER IS SIGNIFICANT: a later net may read a value an earlier
net wrote or cleared (e.g. a net that nulls a sector-word employer must run
before one that re-derives the occupation category from it). Keep new entries
in the group that matches their provenance.
"""
from __future__ import annotations

import pandas as pd

from fec.log import get_logger

from .committee import (
    _classify_committee_types,
    _fix_committee_employer,
    _fix_organization_employer,
    _fix_individual_committee_type,
)
from .occupation import (
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
    _fix_unknown_category,
    _fix_web_artifact_occupation,
    _null_junk_occupation,
    _reclassify_other_category,
)
from .employer import (
    _clear_admin_note_employers,
    _clear_orphan_normalized,
    _clear_refusal_employers,
    _fix_company_name_as_occupation,
    _fix_email_employer_final,
    _fix_employer_equals_occupation,
    _fix_employer_is_occupation_word,
    _fix_filled_but_null_employer,
    _fix_junk_employer_patterns,
    _fix_numeric_employer_final,
    _fix_occ_emp_both_swapped,
    _fix_retired_typos,
    _fix_own_name_as_employer,
    _fix_role_as_employer,
    _swap_role_employer_with_known_company,
    _null_sector_as_employer,
    _fix_self_employed_consistency,
    _fix_swapped_emp_occ_company,
    _fix_truncated_employer_38,
    _null_short_employer_junk,
)
from .names import (
    _fix_choose_prefix,
    _fix_misclassified_foundation,
    _fix_title_as_first_name,
)
from .addresses import (
    _fill_null_city_from_zip,
    _fix_foreign_addresses,
    _fix_garbage_city_names,
    _fix_pr_zip_wrong_state,
)

logger = get_logger(__name__)

__all__ = ["apply_safety_nets"]

# ── Row-scope selectors ──────────────────────────────────────────────
# How each net wants the frame: the whole thing, or masked to the
# individual / non-individual (committee/org) rows. The driver maps these
# to the actual boolean mask, computed once per run.
_ALL = "all"              # fix(df)
_INDIV = "indiv"          # fix(df, is_individual)
_NON_INDIV = "non_indiv"  # fix(df, ~is_individual)

# ── The ordered pipeline of safety nets ──────────────────────────────
# (fix function, scope). ORDER IS SIGNIFICANT — see module docstring.
# Groups mirror how the fixes accreted over successive data-quality audits.
_SAFETY_NETS = [
    # ── Committee / occupation / employer consistency ──
    (_fix_committee_employer,           _NON_INDIV),
    (_fix_organization_employer,        _ALL),
    (_fix_individual_committee_type,    _INDIV),
    (_classify_committee_types,         _NON_INDIV),
    (_fix_disclosed_no_employer,        _ALL),
    (_fix_employed_no_category,         _ALL),
    (_fix_filled_but_null_employer,     _INDIV),
    (_fix_status_word_in_occupation,    _INDIV),
    (_fix_not_disclosed_with_real_occ,  _INDIV),
    (_fill_null_occupation_category,    _INDIV),
    (_fill_null_city_from_zip,          _ALL),
    (_fix_bitton_edge_case,             _INDIV),
    (_clear_refusal_employers,          _INDIV),
    (_clear_admin_note_employers,       _INDIV),
    (_clear_orphan_normalized,          _ALL),
    (_null_short_employer_junk,         _INDIV),
    (_fix_unknown_category,             _ALL),

    # ── Post-scan fixes (from data quality audit) ──
    (_fix_numeric_employer_final,       _INDIV),
    (_fix_email_employer_final,         _INDIV),
    (_fix_junk_employer_patterns,       _INDIV),
    (_fix_retired_typos,                _INDIV),
    (_fix_employer_equals_occupation,   _INDIV),
    (_fix_employer_is_occupation_word,  _INDIV),
    (_fix_misclassified_foundation,     _ALL),
    (_fix_title_as_first_name,          _ALL),
    (_fix_choose_prefix,                _ALL),
    # MUST precede AD: AD collapses a bare title to SELF-EMPLOYED, which
    # would strand the real company sitting in the occupation field.
    (_swap_role_employer_with_known_company, _ALL),
    (_fix_role_as_employer,             _ALL),
    (_null_sector_as_employer,          _ALL),
    (_fix_self_employed_consistency,    _ALL),
    (_fix_own_name_as_employer,         _ALL),

    # ── Address hygiene + truncated-employer repair ──
    (_fix_foreign_addresses,            _ALL),
    (_fix_garbage_city_names,           _ALL),
    (_fix_pr_zip_wrong_state,           _ALL),
    (_fix_truncated_employer_38,        _ALL),

    # ── Occupation-field junk: company names, swaps, web artifacts ──
    (_fix_company_name_as_occupation,   _ALL),
    (_fix_swapped_emp_occ_company,      _ALL),
    (_fix_not_disclosed_in_other,       _ALL),
    (_fix_occ_emp_both_swapped,         _ALL),
    (_fix_web_artifact_occupation,      _ALL),
    (_null_junk_occupation,             _ALL),
    (_fix_employed_as_occupation,       _ALL),

    # ── Cross-field emp/occ/category consistency ──
    (_fix_emp_occ_category_consistency, _ALL),
    (_fix_slash_occupation,             _ALL),
    (_reclassify_other_category,        _ALL),
]


def apply_safety_nets(df: pd.DataFrame, verbose: bool = True) -> int:
    """
    Fix remaining inconsistencies after all enhancement steps.

    Runs every net in ``_SAFETY_NETS`` in order. ``is_individual`` is
    pre-computed once (instead of per net) because no safety-net step
    mutates entity_type or is_individual.

    Returns: total number of fixes applied.
    """
    log = logger.info if verbose else lambda msg: None

    # Pre-compute once — safety nets never change entity classification.
    is_indiv: pd.Series = df['is_individual'].astype(bool)
    not_indiv: pd.Series = ~is_indiv

    # committee_type is an internal working column (dropped from the output at
    # save). It isn't in OUTPUT_COLUMNS, so it may be absent here — the
    # committee_type safety nets need it to exist to read/write.
    if 'committee_type' not in df.columns:
        df['committee_type'] = pd.Series(pd.NA, index=df.index, dtype='object')

    n = 0
    for fix, scope in _SAFETY_NETS:
        if scope is _ALL:
            n += fix(df)
        elif scope is _INDIV:
            n += fix(df, is_indiv)
        else:  # _NON_INDIV
            n += fix(df, not_indiv)

    if n:
        log(f"Safety net: fixed {n:,} remaining inconsistencies")
    return n
