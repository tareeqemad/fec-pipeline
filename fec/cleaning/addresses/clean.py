"""Address cleaning stage."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.cities import clean_cities
from fec.cleaning.addresses.fixes.recovery import _trim_street_to_donor_short_form
from fec.cleaning.addresses.fixes.safe_text import apply_safe_fixes
from fec.cleaning.addresses.fixes.same_street import _recover_address_from_same_street
from fec.cleaning.addresses.fixes.state_zip import (
    _fix_impossible_city_states,
    _fix_state_zip_mismatches,
)
from fec.cleaning.addresses.fixes.unify import (
    _unify_street_spacing,
    _unify_street_spellings,
    _unify_street_types,
    _unify_unit_designators,
)
from fec.cleaning.addresses.fixes.verified import apply_verified_address_fixes
from fec.cleaning.addresses.review import apply_street2_fixes
from fec.cleaning.addresses.streets import clean_streets
from fec.cleaning.addresses.zips import clean_zips
from fec.cleaning.reports.trail import (
    ADDRESS_FIELDS,
    PLACE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)


def _street_reason(df: pd.DataFrame) -> pd.Series:
    reasons = pd.Series("street_text_normalized_or_placeholder_nulled", index=df.index, dtype="object")
    if "_street_email_in_s1" in df.columns:
        flagged = df["_street_email_in_s1"].fillna(False).astype(bool)
        swapped = df["_street_swapped_from_s2"].fillna(False).astype(bool)
        nulled = df["_street_nulled_email"].fillna(False).astype(bool)
        reasons[flagged & swapped] = "street_swap_due_to_email_in_street1"
        reasons[flagged & nulled] = "street_nulled_due_to_email_in_street1"
    if "_street_unit_leftover" in df.columns:
        leftover = df["_street_unit_leftover"].fillna("").astype(str)
        reasons = reasons.mask(leftover.ne(""), leftover)
    return reasons


def _address_rule_source(df: pd.DataFrame) -> pd.Series:
    if "_address_rule" not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="object")
    return df["_address_rule"]


def _clean_street_text(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Normalize street text, then apply the safe text fixes."""
    df = trail.run(
        df, clean_streets, "streets_normalize", _street_reason, STREET_FIELDS,
    )
    df = trail.run(
        df, apply_safe_fixes, "streets_safe_fixes",
        "care_of_house_number_or_trailing_unit_fixed", ADDRESS_FIELDS,
    )
    return df


# Recovered streets are not evidence for matching donors.
INFERRED_STREET_STEPS = frozenset({"streets_trim_truncated"})


def _clean_city_zip(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Normalize city names and ZIP codes."""
    df = trail.run(
        df, clean_cities,
        "cities_normalize", "city_alias_typo_or_punctuation_normalized", PLACE_FIELDS,
    )
    df = trail.run(
        df, clean_zips, "zips_normalize", "zip_reduced_to_five_digits_or_invalid_nulled",
        PLACE_FIELDS,
    )
    return df


# Address alignment passes, in order.
_ALIGN_STEPS = (
    (_fix_impossible_city_states, "address_city_state_conflict",
     "state_fixed_from_person_history_same_city", PLACE_FIELDS),
    (_fix_state_zip_mismatches, "address_zip_state_conflict",
     "state_fixed_or_zip_nulled_by_city_witness", PLACE_FIELDS),
    (_unify_street_spellings, "streets_unify_spelling",
     "street_spelling_unified_to_donor_dominant", STREET_FIELDS),
    (_unify_street_types, "streets_unify_types",
     "street_type_unified_to_donor_dominant", STREET_FIELDS),
    (_unify_street_spacing, "streets_unify_spacing",
     "street_spacing_unified_to_donor_dominant", STREET_FIELDS),
    (_recover_address_from_same_street, "address_same_street_align",
     "aligned_to_donor_dominant_at_same_street", PLACE_FIELDS),
    (_unify_unit_designators, "streets_unify_units",
     "unit_designator_unified_to_donor_dominant", STREET_FIELDS),
)


def _report_address_issues(
    df: pd.DataFrame, trail: AuditTrail, reports: dict | None = None,
) -> pd.DataFrame:
    df, auto_fixed, _ = trail.run(
        df, apply_street2_fixes,
        "address_review", "street_2_nulled_unit_keyword_or_state_only", STREET_FIELDS,
    )
    if reports is not None:
        # clean_pipeline writes the queues after the donor stage
        reports["street2_auto_fixed"] = auto_fixed
    return df


def clean_addresses(
    df: pd.DataFrame, trail: AuditTrail, reports: dict | None = None,
) -> pd.DataFrame:
    """Clean addresses and collect review details for the final rows."""
    df = _clean_street_text(df, trail)
    trail.run(
        df, _trim_street_to_donor_short_form, "streets_trim_truncated",
        "filed_street_tail_trimmed", STREET_FIELDS,
    )
    df = _clean_city_zip(df, trail)
    trail.run(
        df, lambda frame: apply_verified_address_fixes(frame, city_only=False, warn_unused=True), "address_verified_rules",
        "verified_postal_correction", ADDRESS_FIELDS, source=_address_rule_source,
    )
    for transform, step, reason, fields in _ALIGN_STEPS:
        trail.run(df, transform, step, reason, fields)
    trail.run(
        df, lambda frame: apply_verified_address_fixes(frame, city_only=True, warn_unused=True), "address_verified_rules",
        "verified_postal_correction", ADDRESS_FIELDS, source=_address_rule_source,
    )
    return _report_address_issues(df, trail, reports)
