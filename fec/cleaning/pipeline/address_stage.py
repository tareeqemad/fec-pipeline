"""Address cleaning stage."""

from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.cities import clean_cities
from fec.cleaning.addresses.fixes.recovery import (
    _recover_nonstreet_from_donor,
    _recover_null_streets,
    _trim_street_to_donor_short_form,
)
from fec.cleaning.addresses.fixes.house_numbers import _recover_house_number_from_donor
from fec.cleaning.addresses.fixes.safe_text import apply_safe_fixes
from fec.cleaning.addresses.fixes.same_street import (
    _recover_address_from_same_street,
)
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
from fec.cleaning.addresses.review import (
    apply_street2_fixes,
)
from fec.cleaning.addresses.streets import clean_streets
from fec.cleaning.addresses.zips import clean_zips
from fec.cleaning.audit_trail import (
    ADDRESS_FIELDS,
    PLACE_FIELDS,
    STREET_FIELDS,
    AuditTrail,
)
from fec.cleaning.pipeline.fec_recovery import recover_addresses_from_fec


# per-row street-normalize reason, flags email-in-street1 cases
def _street_reason(df: pd.DataFrame) -> pd.Series:
    reasons = pd.Series("street_text_normalized_or_placeholder_nulled", index=df.index, dtype="object")
    if "_street_email_in_s1" in df.columns:
        flagged = df["_street_email_in_s1"].fillna(False).astype(bool)
        swapped = df["_street_swapped_from_s2"].fillna(False).astype(bool)
        nulled = df["_street_nulled_email"].fillna(False).astype(bool)
        reasons[flagged & swapped] = "street_swap_due_to_email_in_street1"
        reasons[flagged & nulled] = "street_nulled_due_to_email_in_street1"
    return reasons


# per-row source label for verified address rule fixes
def _address_rule_source(df: pd.DataFrame) -> pd.Series:
    if "_address_rule" not in df.columns:
        return pd.Series(pd.NA, index=df.index, dtype="object")
    return df["_address_rule"]


# normalize street text, then apply safe deterministic fixes
def _clean_street_text(df: pd.DataFrame, trail: AuditTrail, log) -> pd.DataFrame:
    """Normalize street text, then apply the safe text fixes."""
    df = trail.run_logged(
        df, clean_streets, "streets_normalize", _street_reason, STREET_FIELDS,
        "Streets: {streets_normalized:,} normalized, {units_extracted:,} units extracted",
        log, always=True,
    )
    return trail.run_logged(
        df, apply_safe_fixes, "streets_safe_fixes",
        "care_of_house_number_or_trailing_unit_fixed", ADDRESS_FIELDS,
        "Streets: {house_number:,} house-number/dup fixes, "
        "{unit_split:,} trailing units split, {care_of:,} C/O prefixes stripped",
        log,
    )


# Street recoveries from the donor's own filings, in order
_STREET_RECOVERIES = (
    (_recover_null_streets, "streets_recover_null",
     "street_filled_from_donor_same_city",
     "Streets: recovered {n:,} from other records of same donor"),
    (_recover_nonstreet_from_donor, "streets_recover_nonstreet",
     "nonstreet_replaced_from_donor_history",
     "Streets: recovered {n:,} non-street fragments from same donor"),
    (_recover_house_number_from_donor, "streets_recover_house_number",
     "house_number_backfilled_from_donor_history",
     "Streets: backfilled {n:,} missing house numbers from same donor"),
    (_trim_street_to_donor_short_form, "streets_trim_truncated",
     "truncated_street_fragment_replaced_by_donor_short_form",
     "Streets: trimmed {n:,} truncated streets back to the donor's short form"),
)


# fill missing or broken streets from donor, then fec.gov
def _recover_streets(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> None:
    """Fill missing or broken streets from the donor, then FEC."""
    for recovery, step, reason, message in _STREET_RECOVERIES:
        trail.run_logged(df, recovery, step, reason, STREET_FIELDS, message, log)

    trail.run_logged(
        df, lambda frame: recover_addresses_from_fec(frame, out_dir),
        "streets_recover_fec_api", "address_recovered_from_fec_api_other_committees",
        ADDRESS_FIELDS, "Streets: recovered {n:,} from FEC.gov (other committees, cached)", log,
    )


# normalize city names, then clean zip codes
def _clean_city_zip(df: pd.DataFrame, trail: AuditTrail, out_dir, log) -> pd.DataFrame:
    """Normalize city names and ZIP codes."""
    df = trail.run_logged(
        df, lambda frame: clean_cities(frame, fuzzy=True, report_dir=out_dir),
        "cities_normalize", "city_alias_typo_or_punctuation_normalized", PLACE_FIELDS,
        "Cities: {known_fixes:,} known fixes, {fuzzy_fixes:,} fuzzy fixes, "
        "{punctuation_cleaned:,} punctuation cleaned",
        log, always=True,
    )
    return trail.run_logged(
        df, clean_zips, "zips_normalize", "zip_reduced_to_five_digits_or_invalid_nulled",
        PLACE_FIELDS, "ZIPs: {cleaned:,} cleaned, {invalid_nulled:,} invalid -> null",
        log, always=True,
    )


# Address alignment passes, in order: (transform, audit step, reason, fields, log line)
_ALIGN_STEPS = (
    (_fix_impossible_city_states, "address_city_state_conflict",
     "state_fixed_from_person_history_same_city", PLACE_FIELDS,
     "City-state: {n:,} impossible states fixed (city is the witness)"),
    (_fix_state_zip_mismatches, "address_zip_state_conflict",
     "state_fixed_or_zip_nulled_by_city_witness", PLACE_FIELDS,
     "State-ZIP: {state_fixed:,} states fixed (ZIP kept), {zip_nulled:,} ZIPs nulled (state kept)"),
    (_unify_street_spellings, "streets_unify_spelling",
     "street_spelling_unified_to_donor_dominant", STREET_FIELDS,
     "Streets: unified {n:,} spelling variants (same donor + same address)"),
    (_unify_street_types, "streets_unify_types",
     "street_type_unified_to_donor_dominant", STREET_FIELDS,
     "Streets: unified {n:,} with/without-type variants (same donor)"),
    (_unify_street_spacing, "streets_unify_spacing",
     "street_spacing_unified_to_donor_dominant", STREET_FIELDS,
     "Streets: unified {n:,} spacing/punctuation variants (same address)"),
    (_recover_address_from_same_street, "address_same_street_align",
     "aligned_to_donor_dominant_at_same_street", PLACE_FIELDS,
     "Same-street: {zip:,} ZIPs filled, {city:,} cities + {state:,} states aligned"),
    (_unify_unit_designators, "streets_unify_units",
     "unit_designator_unified_to_donor_dominant", STREET_FIELDS,
     "Streets: unified {n:,} unit-designator variants (same donor + same unit)"),
)


# align each donor's city, state, zip, and street spelling
def _align_address_parts(df: pd.DataFrame, trail: AuditTrail, log) -> None:
    """Align each donor's city, state, ZIP and street spellings."""
    for transform, step, reason, fields, message in _ALIGN_STEPS:
        trail.run_logged(df, transform, step, reason, fields, message, log)


# log counts of rows left for address review
def log_review_queues(counts: dict, log) -> None:
    if counts["manual_review"] or counts["auto_fixed"] or counts["regeocode"]:
        log(
            f"Address review: {counts['manual_review']:,} open for manual review, "
            f"{counts['auto_fixed']:,} emptied street_2 listed as auto_fixed, "
            f"{counts['regeocode']:,} for re-geocoding"
        )


# flag remaining street_2 issues for manual review
def _report_address_issues(
    df: pd.DataFrame, trail: AuditTrail, log, reports: dict | None = None,
) -> pd.DataFrame:
    df, auto_fixed, emptied = trail.run(
        df, apply_street2_fixes,
        "address_review", "street_2_nulled_unit_keyword_or_state_only", STREET_FIELDS,
    )
    if emptied:
        log(f"Address review: {emptied:,} bad street_2 emptied")
    if reports is not None:
        # clean_pipeline writes the queues after the donor stage
        reports["street2_auto_fixed"] = auto_fixed
    return df


# run the full address cleaning stage end-to-end
def clean_addresses(
    df: pd.DataFrame, trail: AuditTrail, out_dir, log, reports: dict | None = None,
) -> pd.DataFrame:
    """Run the whole address stage.

    With ``reports`` the review queues are not written here: the street_2 values
    the stage emptied are stored in it for clean_pipeline, which writes the queues
    from the final rows.
    """
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
    return _report_address_issues(df, trail, log, reports)
