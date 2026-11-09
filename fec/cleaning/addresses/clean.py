"""Address cleaning stage: the standard form, then the reviewed and per-donor fixes."""

from __future__ import annotations

import pandas as pd

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
from fec.cleaning.addresses.standardize import clear_own_name_street, standardize_addresses
from fec.cleaning.addresses.zips import clean_zips
from fec.cleaning.reports.trail import (
    ADDRESS_FIELDS,
    PLACE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)


def _address_rule_source(df: pd.DataFrame) -> pd.Series:
    if "_address_rule" not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="object")
    return df["_address_rule"]


# Per-donor alignment passes, in order.
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


def clean_addresses(df: pd.DataFrame, trail: AuditTrail) -> pd.DataFrame:
    """Standard form from the model, own-name streets cleared, ZIPs checked, rules and alignment applied."""
    trail.run(df, standardize_addresses, "address_standard_form", "standard_form_from_model", ADDRESS_FIELDS)
    trail.run(df, clear_own_name_street, "streets_own_name", "street_is_the_donor_name_nulled", STREET_FIELDS)
    trail.run(df, clean_zips, "zips_normalize", "zip_reduced_to_five_digits_or_invalid_nulled", PLACE_FIELDS)
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
    return df
