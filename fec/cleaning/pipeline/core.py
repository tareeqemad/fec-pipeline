"""Clean records, identify donors, and standardize their history."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from fec.cleaning.address_review import apply_safe_fixes, build_address_reports
from fec.cleaning.addresses import clean_cities, clean_streets, clean_zips
from fec.cleaning.occupations import clean_employer_occupation
from fec.config.data import MISSING_VALUES, OUTPUT_COLUMNS
from fec.config.geography import US_STATES
from fec.log import get_logger

from .address_fixes import (
    _fix_impossible_city_states,
    _fix_state_zip_mismatches,
    _recover_address_from_same_street,
    _recover_house_number_from_donor,
    _recover_nonstreet_from_donor,
    _recover_null_streets,
    _unify_street_spacing,
    _unify_street_spellings,
    _unify_unit_designators,
)
from .fec_recovery import recover_addresses_from_fec
from .names import _clean_names
from .reclassify import _reclassify_entities, _restore_reclassified_committees
from .reports import _build_missing_report, _sanity_check

logger = get_logger(__name__)

def _prepare_records(df: pd.DataFrame, log) -> pd.DataFrame:
    from fec.donor_match.scoring import extract_generational_suffix

    df["_generational_suffix"] = df["contributor_name"].map(
        extract_generational_suffix
    )
    dup_count = int(df["sub_id"].duplicated().sum())
    if dup_count:
        log(f"WARNING: Duplicates: {dup_count} duplicate sub_ids -> dropping")
        df = df.drop_duplicates(subset="sub_id", keep="first").copy()
    else:
        log("Duplicates: none")

    df["contributor_state"] = (
        df["contributor_state"].astype(str).str.strip().str.upper()
    )
    before = len(df)
    df = df[df["contributor_state"].isin(US_STATES)].copy()
    dropped = before - len(df)
    log(f"State filter: {before:,} -> {len(df):,} (dropped {dropped:,})")

    # record missing occupation/employer before later steps fill them
    def _mark_missing(field):
        raw = df[field].astype("string").fillna("").str.strip().str.upper()
        return df[field].isna() | (raw == "") | raw.isin(MISSING_VALUES)

    df["_occ_missing"] = _mark_missing("contributor_occupation")
    df["_emp_missing"] = _mark_missing("contributor_employer")

    df["contribution_receipt_date"] = pd.to_datetime(
        df["contribution_receipt_date"], errors="coerce"
    )

    warnings = _sanity_check(df)
    if warnings:
        log(f"Sanity: {'; '.join(warnings)}")
    else:
        log("Sanity: all amounts & dates OK")
    raw = df["is_individual"].astype("string").fillna("").str.strip().str.lower()
    df["is_individual"] = raw.isin(("true", "t", "1", "yes"))
    return df


def _clean_people(df: pd.DataFrame, log) -> pd.DataFrame:
    # Reclassification restores raw work fields.
    raw_occupations = df["contributor_occupation"].copy()
    raw_employers = df["contributor_employer"].copy()
    df, occ_counts = clean_employer_occupation(df)
    log(
        f"Occupations: {occ_counts['normalized']:,} normalized, "
        f"{occ_counts['occ_fixed']:,} typo-fixed, {occ_counts['comm_filled']:,} committees filled"
    )

    n_to_indiv, n_to_comm = _reclassify_entities(df)
    log(
        f"Reclassified {n_to_indiv:,} committee -> INDIVIDUAL, {n_to_comm:,} individual -> COMMITTEE"
    )
    _restore_reclassified_committees(df, raw_occupations, raw_employers, log)
    _clean_names(df)
    return df


def _clean_street_text(df: pd.DataFrame, log) -> pd.DataFrame:
    df, street_counts = clean_streets(df)
    log(
        f"Streets: {street_counts['streets_normalized']:,} normalized, "
        f"{street_counts['units_extracted']:,} units extracted"
    )

    df, safe_counts = apply_safe_fixes(df)
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


def _recover_streets(df: pd.DataFrame, out_dir, log) -> None:
    recoveries = (
        (_recover_null_streets, "recovered", "from other records of same donor"),
        (
            _recover_nonstreet_from_donor,
            "recovered",
            "non-street fragments from same donor",
        ),
        (
            _recover_house_number_from_donor,
            "backfilled",
            "missing house numbers from same donor",
        ),
    )
    for recovery, action, description in recoveries:
        changed = recovery(df)
        if changed:
            log(f"Streets: {action} {changed:,} {description}")

    fec_rows = recover_addresses_from_fec(df, out_dir)
    if fec_rows:
        log(f"Streets: recovered {fec_rows:,} from FEC.gov (other committees, cached)")


def _clean_city_zip(df: pd.DataFrame, fuzzy_city: bool, out_dir, log) -> pd.DataFrame:
    df, city_counts = clean_cities(df, fuzzy=fuzzy_city, report_dir=out_dir)
    log(
        f"Cities: {city_counts['known_fixes']:,} known fixes, "
        f"{city_counts['fuzzy_fixes']:,} fuzzy fixes, "
        f"{city_counts['punctuation_cleaned']:,} punctuation cleaned"
    )

    df, zip_counts = clean_zips(df)
    log(
        f"ZIPs: {zip_counts['cleaned']:,} cleaned, {zip_counts['invalid_nulled']:,} invalid -> null"
    )
    return df


def _align_address_parts(df: pd.DataFrame, log) -> None:
    n_city_state = _fix_impossible_city_states(df)
    if n_city_state:
        log(
            f"City-state: {n_city_state:,} impossible states fixed (city is the witness)"
        )

    zip_state_counts = _fix_state_zip_mismatches(df)
    if zip_state_counts["state_fixed"] or zip_state_counts["zip_nulled"]:
        log(
            f"State-ZIP: {zip_state_counts['state_fixed']:,} states fixed (ZIP kept), "
            f"{zip_state_counts['zip_nulled']:,} ZIPs nulled (state kept)"
        )

    n_street_spell = _unify_street_spellings(df)
    if n_street_spell:
        log(
            f"Streets: unified {n_street_spell:,} spelling variants (same donor + same address)"
        )
    n_street_space = _unify_street_spacing(df)
    if n_street_space:
        log(
            f"Streets: unified {n_street_space:,} spacing/punctuation variants (same address)"
        )

    recovered = _recover_address_from_same_street(df)
    if recovered["zip"] or recovered["city"] or recovered["state"]:
        log(
            f"Same-street: {recovered['zip']:,} ZIPs filled, "
            f"{recovered['city']:,} cities + {recovered['state']:,} states aligned"
        )

    n_unit = _unify_unit_designators(df)
    if n_unit:
        log(
            f"Streets: unified {n_unit:,} unit-designator variants (same donor + same unit)"
        )


def _report_address_issues(df: pd.DataFrame, out_dir, log) -> pd.DataFrame:
    df, review_counts = build_address_reports(df, out_dir)
    if review_counts["manual_review"] or review_counts["regeocode"]:
        log(
            f"Address review: {review_counts['manual_review']:,} flagged for manual review, "
            f"{review_counts['regeocode']:,} for re-geocoding "
            f"({review_counts['street2_emptied']:,} bad street_2 emptied)"
        )
    return df


def _clean_addresses(df: pd.DataFrame, fuzzy_city: bool, out_dir, log) -> pd.DataFrame:
    df = _clean_street_text(df, log)
    _recover_streets(df, out_dir, log)
    df = _clean_city_zip(df, fuzzy_city, out_dir, log)
    _align_address_parts(df, log)
    return _report_address_issues(df, out_dir, log)


def _finish_records(df: pd.DataFrame, audit: bool, log):
    from fec.committees import committee_id_to_name

    names = committee_id_to_name()
    df["recipient_committee"] = df["committee_id"].map(names).fillna(df["committee_id"])
    unknown = sorted(
        set(
            df.loc[
                df["committee_id"].notna() & ~df["committee_id"].isin(names),
                "committee_id",
            ]
        )
    )
    if unknown:
        log(f"WARNING: committee_id(s) not in committees.csv (kept raw): {unknown}")

    missing = _build_missing_report(df)

    columns = [column for column in OUTPUT_COLUMNS if column in df.columns]
    if audit:
        internal_cols = [
            "_reclass_reason",
            "_street_email_in_s1",
            "_street_swapped_from_s2",
            "_street_nulled_email",
            "_garbled_before",
        ]
        columns += [column for column in internal_cols if column in df.columns]
    return df[columns], missing


def _clean_fields(
    df: pd.DataFrame,
    verbose: bool = True,
    fuzzy_city: bool = True,
    out_dir: str | None = None,
    audit: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean contribution fields before donor matching."""
    start = time.time()
    log = logger.info if verbose else lambda _message: None

    df = _prepare_records(df, log)
    df = _clean_people(df, log)
    df = _clean_addresses(df, fuzzy_city, out_dir, log)
    df, missing = _finish_records(df, audit, log)
    elapsed = time.time() - start
    log(f"Done: {len(df):,} rows in {elapsed:.1f}s")
    return df, missing


def _validate_generational_suffixes(df: pd.DataFrame) -> None:
    """Reject a donor containing different explicit generation suffixes."""
    people = df[df["entity_type"] == "INDIVIDUAL"]
    suffixes = people[people["_generational_suffix"] != ""]
    conflicts = suffixes.groupby("donor_key")["_generational_suffix"].nunique()
    conflicts = conflicts[conflicts > 1]
    if not conflicts.empty:
        keys = ", ".join(conflicts.index.astype(str)[:3])
        raise ValueError(f"JR/SR identity guard rejected donor(s): {keys}")


def clean_records(
    df: pd.DataFrame,
    fuzzy_city: bool = True,
    out_dir: str | None = None,
    audit: bool = False,
):
    """Clean every contribution record."""
    df_clean, missing = _clean_fields(
        df,
        fuzzy_city=fuzzy_city,
        out_dir=out_dir,
        audit=audit,
    )

    logger.info("\n-- Enhancements --")
    from fec.cleaning.enhancements import run_enhancements

    df_clean, enh_audit = run_enhancements(df_clean)

    # Reapply curated overrides.
    from fec.cleaning.manual_overrides import apply_manual_employer_overrides

    n_overrides = apply_manual_employer_overrides(df_clean)
    if n_overrides:
        logger.info(f"  Manual employer overrides applied: {n_overrides:,} rows")

    return df_clean, missing, enh_audit


def identify_donors(
    df_clean: pd.DataFrame, out_dir: str | None = None
) -> pd.DataFrame:
    """Assign one donor_key to each identity."""
    df_clean = df_clean.reset_index(drop=True)
    if "_generational_suffix" not in df_clean.columns:
        df_clean["_generational_suffix"] = ""
    logger.info("\n-- Donor identity --")
    from fec.donor_match import (
        apply_curated_key_merges,
        apply_donor_key,
        match_donors,
        merge_split_name_donors,
        validate_separations,
    )

    rid_to_key, match_audit = match_donors(df_clean, verbose=False)
    df_clean = apply_donor_key(df_clean, rid_to_key)

    repointed = merge_split_name_donors(df_clean)
    repointed += apply_curated_key_merges(df_clean)
    _validate_generational_suffixes(df_clean)
    validate_separations(df_clean)

    profiles = len(rid_to_key)
    donors = df_clean.loc[
        df_clean["entity_type"].eq("INDIVIDUAL"), "donor_key"
    ].nunique()
    logger.info(
        "  %s profiles -> %s donors (%s merged; %s pairs scored)",
        f"{profiles:,}",
        f"{donors:,}",
        f"{profiles - donors:,}",
        f"{len(match_audit):,}",
    )
    if repointed:
        logger.info("  %s rows joined by identity rules", f"{repointed:,}")
    return df_clean


def standardize_donors(
    df_clean: pd.DataFrame, out_dir: str | None = None
) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    df_clean = df_clean.reset_index(drop=True)
    logger.info("\n-- Donor consistency --")
    from fec.donor_match import (
        align_org_donor_company_names,
        build_donor_dedup_review,
        canonicalize_donor_addresses,
        canonicalize_donor_employers,
        canonicalize_donor_names,
        canonicalize_donor_pobox_typos,
        canonicalize_donor_units,
    )

    canonicalizers = (
        canonicalize_donor_names,
        canonicalize_donor_employers,
        align_org_donor_company_names,
        canonicalize_donor_addresses,
        canonicalize_donor_units,
        canonicalize_donor_pobox_typos,
    )
    canonical_updates = 0
    for fix in canonicalizers:
        count = fix(df_clean)
        canonical_updates += count
        if count:
            label = fix.__name__.lstrip("_").replace("_", " ")
            logger.info("  %-38s %s", label, f"{count:,}")

    from fec.cleaning.entity_classification import apply_name_corrections

    df_clean, manual_updates = apply_name_corrections(df_clean)
    if manual_updates:
        logger.info("  %-38s %s", "curated name corrections", f"{manual_updates:,}")

    from fec.cleaning.donor_consistency import apply_donor_consistency

    consistency_updates = apply_donor_consistency(df_clean)

    from fec.cleaning.employer_synonyms import finalize_employer_names

    df_clean, employer_updates = finalize_employer_names(df_clean)
    if employer_updates:
        logger.info("  %-38s %s", "final employer names", f"{employer_updates:,}")
    final_canonical = canonicalize_donor_employers(df_clean)
    employer_updates += final_canonical
    if final_canonical:
        logger.info("  %-38s %s", "final donor employers", f"{final_canonical:,}")
    canonical_updates += employer_updates

    from fec.cleaning.manual_overrides import apply_manual_employer_overrides

    protected = apply_manual_employer_overrides(df_clean, company_names_only=True)
    if protected:
        logger.info("  %-38s %s", "curated employer names", f"{protected:,}")
        canonical_updates += protected
    build_donor_dedup_review(df_clean, out_dir)

    name_cols = [
        column
        for column in ("contributor_first_name", "contributor_last_name")
        if column in df_clean.columns
    ]
    cleared = 0
    if "entity_type" in df_clean.columns and name_cols:
        mask = (df_clean["entity_type"] != "INDIVIDUAL") & df_clean[
            name_cols
        ].notna().any(axis=1)
        if mask.any():
            df_clean.loc[mask, name_cols] = np.nan
            cleared = int(mask.sum())
            logger.info("  %-38s %s", "non-individual names cleared", f"{cleared:,}")

    logger.info(
        "  %s canonical updates, %s consistency updates",
        f"{canonical_updates + manual_updates + cleared:,}",
        f"{consistency_updates:,}",
    )

    return df_clean


def clean_pipeline(
    df: pd.DataFrame,
    fuzzy_city: bool = True,
    out_dir: str | None = None,
    audit: bool = False,
):
    """Run the complete cleaning pipeline."""
    df_clean, missing, enh_audit = clean_records(
        df,
        fuzzy_city=fuzzy_city,
        out_dir=out_dir,
        audit=audit,
    )
    df_clean = identify_donors(df_clean, out_dir=out_dir)
    df_clean = standardize_donors(df_clean, out_dir=out_dir)
    return df_clean, missing, enh_audit
