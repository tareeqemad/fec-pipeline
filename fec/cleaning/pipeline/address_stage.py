"""Address cleaning stage."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.address_review import build_address_reports
from fec.cleaning.addresses import clean_cities, clean_streets, clean_zips
from fec.cleaning.audit_trail import (
    ADDRESS_FIELDS,
    PLACE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)

from .address_fixes import (
    _fix_impossible_city_states,
    _fix_state_zip_mismatches,
    _recover_address_from_same_street,
    _recover_house_number_from_donor,
    _recover_nonstreet_from_donor,
    _recover_null_streets,
    _trim_street_to_donor_short_form,
    _unify_street_spacing,
    _unify_street_spellings,
    _unify_street_types,
    _unify_unit_designators,
)
from .address_fixes.safe_text import apply_safe_fixes
from .address_fixes.verified import apply_verified_address_fixes
from .fec_recovery import recover_addresses_from_fec

def _street_reason(df: pd.DataFrame) -> pd.Series:
    reasons = pd.Series("street_text_normalized_or_placeholder_nulled", index=df.index, dtype="object")
    if "_street_email_in_s1" in df.columns:
        flagged = df["_street_email_in_s1"].fillna(False).astype(bool)
        swapped = df["_street_swapped_from_s2"].fillna(False).astype(bool)
        nulled = df["_street_nulled_email"].fillna(False).astype(bool)
        reasons[flagged] = "street_email_in_street1"
        reasons[flagged & swapped] = "street_swap_due_to_email_in_street1"
        reasons[flagged & nulled] = "street_nulled_due_to_email_in_street1"
    return reasons


def _address_rule_source(df: pd.DataFrame) -> pd.Series:
    if "_address_rule" not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="object")
    return df["_address_rule"]


def _clean_street_text(df: pd.DataFrame, trail: AuditTrail, log) -> pd.DataFrame:
    df, street_counts = trail.run(
        df, clean_streets, "streets_normalize", _street_reason, STREET_FIELDS,
    )
    log(
        f"Streets: {street_counts['streets_normalized']:,} normalized, "
        f"{street_counts['units_extracted']:,} units extracted"
    )

    df, safe_counts = trail.run(
        df, apply_safe_fixes, "streets_safe_fixes",
        "care_of_house_number_or_trailing_unit_fixed", ADDRESS_FIELDS,
    )
    if (
        safe_counts["house_number"]
        or safe_counts["unit_split"]
        or safe_counts["care_of"]
    ):
        log(
            f"Streets: {safe_counts['house_number']:,} house-number/dup fixes, "
            f"{safe_counts['unit_split']:,} trailing units split, "
            f"{safe_counts['care_of']:,} C/O prefixes stripped"
        )
    return df


def _recover_streets(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> None:
    recoveries = (
        (_recover_null_streets, "streets_recover_null",
         "street_filled_from_donor_same_city",
         "recovered", "from other records of same donor"),
        (_recover_nonstreet_from_donor, "streets_recover_nonstreet",
         "nonstreet_replaced_from_donor_history",
         "recovered", "non-street fragments from same donor"),
        (_recover_house_number_from_donor, "streets_recover_house_number",
         "house_number_backfilled_from_donor_history",
         "backfilled", "missing house numbers from same donor"),
        (_trim_street_to_donor_short_form, "streets_trim_truncated",
         "truncated_street_fragment_replaced_by_donor_short_form",
         "trimmed", "truncated streets back to the donor's short form"),
    )
    for recovery, step, reason, action, description in recoveries:
        changed = trail.run(df, recovery, step, reason, STREET_FIELDS)
        if changed:
            log(f"Streets: {action} {changed:,} {description}")

    fec_rows = trail.run(
        df, lambda frame: recover_addresses_from_fec(frame, out_dir),
        "streets_recover_fec_api", "address_recovered_from_fec_api_other_committees",
        ADDRESS_FIELDS,
    )
    if fec_rows:
        log(f"Streets: recovered {fec_rows:,} from FEC.gov (other committees, cached)")


def _clean_city_zip(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> pd.DataFrame:
    df, city_counts = trail.run(
        df, lambda frame: clean_cities(frame, fuzzy=True, report_dir=out_dir),
        "cities_normalize", "city_alias_typo_or_punctuation_normalized", PLACE_FIELDS,
    )
    log(
        f"Cities: {city_counts['known_fixes']:,} known fixes, "
        f"{city_counts['fuzzy_fixes']:,} fuzzy fixes, "
        f"{city_counts['punctuation_cleaned']:,} punctuation cleaned"
    )

    df, zip_counts = trail.run(
        df, clean_zips, "zips_normalize", "zip_reduced_to_five_digits_or_invalid_nulled", PLACE_FIELDS,
    )
    log(
        f"ZIPs: {zip_counts['cleaned']:,} cleaned, {zip_counts['invalid_nulled']:,} invalid -> null"
    )
    return df


def _align_address_parts(df: pd.DataFrame, trail: AuditTrail, log) -> None:
    n_city_state = trail.run(
        df, _fix_impossible_city_states, "address_city_state_conflict",
        "state_fixed_from_person_history_same_city", PLACE_FIELDS,
    )
    if n_city_state:
        log(
            f"City-state: {n_city_state:,} impossible states fixed (city is the witness)"
        )

    zip_state_counts = trail.run(
        df, _fix_state_zip_mismatches, "address_zip_state_conflict",
        "state_fixed_or_zip_nulled_by_city_witness", PLACE_FIELDS,
    )
    if zip_state_counts["state_fixed"] or zip_state_counts["zip_nulled"]:
        log(
            f"State-ZIP: {zip_state_counts['state_fixed']:,} states fixed (ZIP kept), "
            f"{zip_state_counts['zip_nulled']:,} ZIPs nulled (state kept)"
        )

    n_street_spell = trail.run(
        df, _unify_street_spellings, "streets_unify_spelling",
        "street_spelling_unified_to_donor_dominant", STREET_FIELDS,
    )
    if n_street_spell:
        log(
            f"Streets: unified {n_street_spell:,} spelling variants (same donor + same address)"
        )
    n_street_type = trail.run(
        df, _unify_street_types, "streets_unify_types",
        "street_type_unified_to_donor_dominant", STREET_FIELDS,
    )
    if n_street_type:
        log(f"Streets: unified {n_street_type:,} with/without-type variants (same donor)")

    n_street_space = trail.run(
        df, _unify_street_spacing, "streets_unify_spacing",
        "street_spacing_unified_to_donor_dominant", STREET_FIELDS,
    )
    if n_street_space:
        log(
            f"Streets: unified {n_street_space:,} spacing/punctuation variants (same address)"
        )

    recovered = trail.run(
        df, _recover_address_from_same_street, "address_same_street_align",
        "aligned_to_donor_dominant_at_same_street", PLACE_FIELDS,
    )
    if recovered["zip"] or recovered["city"] or recovered["state"]:
        log(
            f"Same-street: {recovered['zip']:,} ZIPs filled, "
            f"{recovered['city']:,} cities + {recovered['state']:,} states aligned"
        )

    n_unit = trail.run(
        df, _unify_unit_designators, "streets_unify_units",
        "unit_designator_unified_to_donor_dominant", STREET_FIELDS,
    )
    if n_unit:
        log(
            f"Streets: unified {n_unit:,} unit-designator variants (same donor + same unit)"
        )


def _report_address_issues(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> pd.DataFrame:
    df, review_counts = trail.run(
        df, lambda frame: build_address_reports(frame, out_dir),
        "address_review", "street_2_nulled_unit_keyword_or_state_only", STREET_FIELDS,
    )
    if review_counts["manual_review"] or review_counts["regeocode"]:
        log(
            f"Address review: {review_counts['manual_review']:,} flagged for manual review, "
            f"{review_counts['regeocode']:,} for re-geocoding "
            f"({review_counts['street2_emptied']:,} bad street_2 emptied)"
        )
    return df


def clean_addresses(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> pd.DataFrame:
    """Run the whole address stage."""
    df = _clean_street_text(df, trail, log)
    _recover_streets(df, trail, out_dir, log)
    df = _clean_city_zip(df, trail, out_dir, log)
    verified = trail.run(
        df, apply_verified_address_fixes, "address_verified_rules",
        "verified_postal_correction", ADDRESS_FIELDS, source=_address_rule_source,
    )
    if verified:
        log(f"Streets: applied {verified:,} verified postal corrections")
    _align_address_parts(df, trail, log)
    return _report_address_issues(df, trail, out_dir, log)
