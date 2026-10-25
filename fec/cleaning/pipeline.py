"""Cleaning stages in execution order. Start with clean_pipeline."""
from __future__ import annotations

import time

import pandas as pd

from fec.cleaning.addresses.foreign import (
    kept_as_filed_reason,
    restore_foreign_addresses,
    snapshot_foreign_addresses,
)
from fec.cleaning.addresses.review import write_final_address_reports
from fec.cleaning.reports.trail import (
    ADDRESS_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    STREET_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.donors.steps import INFERRED_WORK_STEPS
from fec.cleaning.entities import (
    _clear_individual_residue,
    _reclassify_entities,
    _restore_reclassified_committees,
    attach_source_fields,
)
from fec.cleaning.rules.manual import (
    FILED_WORK_FIELDS,
    apply_manual_employer_overrides,
    outside_work_sub_ids,
)
from fec.cleaning.work.clean import clean_employer_occupation
from fec.cleaning.addresses.clean import INFERRED_STREET_STEPS, clean_addresses
from fec.cleaning.donors.clean import identify_donors, standardize as standardize_donors
from fec.cleaning.names import _clean_names, _preclean_name_punctuation
from fec.cleaning.reports.audit import _build_missing_report, _sanity_check
from fec.cleaning.rules.records import apply_record_rules
from fec.committees import committee_id_to_name
from fec.config.data import MISSING_VALUES, OUTPUT_COLUMNS
from fec.config.geography import US_STATES
from fec.donor_match.normalize import extract_generational_suffix
from fec.log import get_logger

logger = get_logger(__name__)


# Clean records, match donors and align their filings.
# Write reports to out_dir.
def clean_pipeline(
    df: pd.DataFrame,
    out_dir: str | None = None,
):
    df = df.copy()
    trail = AuditTrail()

    # Preserve filed values before repairs.
    foreign = snapshot_foreign_addresses(df)
    filed_work = df.reindex(columns=list(FILED_WORK_FIELDS)).fillna("").astype(str)
    filed_addresses = df.reindex(columns=["sub_id", *ADDRESS_FIELDS]).fillna("").astype(str)
    address_reports: dict = {}

    df_clean, missing = clean_records(df, trail, address_reports=address_reports)

    # Inferred streets cannot serve as evidence for matching donors.
    df_clean["_street_inferred"] = df_clean["sub_id"].astype(str).isin(
        trail.keys_set_by(INFERRED_STREET_STEPS, STREET_FIELDS)
    )
    df_clean = identify_donors(df_clean)
    df_clean = standardize_donors(df_clean, out_dir=out_dir, trail=trail)

    n_foreign = trail.run(
        df_clean, lambda frame: restore_foreign_addresses(frame, foreign),
        "foreign_address_restore", kept_as_filed_reason, ADDRESS_FIELDS,
    )
    logger.info(
        "  Foreign addresses: %s rows kept as filed (%s cells restored)",
        f"{len(foreign):,}", f"{n_foreign:,}",
    )
    if out_dir:
        write_final_address_reports(
            df_clean, out_dir, address_reports, foreign.index, filed_addresses, trail,
            log=logger.info,
        )

    unexplained = trail.finish(df_clean)
    df_clean["employment_source"] = _employment_sources(df_clean, trail, filed_work)
    if unexplained:
        logger.warning(
            "  Audit: %s field values changed outside tracked steps", f"{unexplained:,}"
        )
    return df_clean, missing, trail


def clean_records(
    df: pd.DataFrame,
    trail: AuditTrail,
    address_reports: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean fields, apply record rules, then apply curated overrides."""
    start = time.time()
    log = logger.info

    df = _prepare_records(df, log)
    trail.start(df)
    df = _clean_people(df, trail, log)
    df = clean_addresses(df, trail, log, reports=address_reports)
    df, missing = _finish_records(df, log)
    elapsed = time.time() - start
    log(f"Done: {len(df):,} rows in {elapsed:.1f}s")

    logger.info("\n-- Record rules --")

    df = apply_record_rules(df, trail)

    n_overrides = trail.run(
        df, apply_manual_employer_overrides, "manual_overrides",
        "curated_row_override", WORK_FIELDS + ("previous_employer",
        "contributor_city", "contributor_street_1", "contributor_street_2", "contributor_zip"),
        source="data/rules/manual_employer_overrides.csv",
    )
    if n_overrides:
        logger.info(f"  Manual employer overrides applied: {n_overrides:,} rows")

    return df, missing


def _prepare_records(df: pd.DataFrame, log) -> pd.DataFrame:
    """Prepare IDs, states, dates and missing-work flags."""
    df["_generational_suffix"] = df["contributor_name"].map(
        extract_generational_suffix
    )
    dup_count = int(df["sub_id"].duplicated().sum())
    if dup_count:
        log(f"WARNING: Duplicates: {dup_count} duplicate sub_ids -> dropping")
        df = df.drop_duplicates(subset="sub_id", keep="first").copy()
    else:
        log("Duplicates: none")

    state = df["contributor_state"].astype("string").str.strip().str.upper()
    df["contributor_state"] = state.where(state.fillna("") != "", pd.NA).astype(object)
    # Keep foreign and unknown states; they do not invalidate a contribution.
    unknown = int((~df["contributor_state"].isin(US_STATES)).sum())
    log(f"States: {unknown:,} filing(s) with a state outside the US list, kept as filed")

    # Record missing work fields before filling them.
    for field, flag in (
        ("contributor_occupation", "_occ_missing"),
        ("contributor_employer", "_emp_missing"),
    ):
        raw = df[field].astype("string").fillna("").str.strip().str.upper()
        df[flag] = df[field].isna() | (raw == "") | raw.isin(MISSING_VALUES)

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
    attach_source_fields(df)
    return df


def _reclassify_reason(df: pd.DataFrame) -> pd.Series:
    reasons = df["_reclass_reason"].astype(object)
    return reasons.where(reasons.notna(), "entity_type_from_business_or_committee_signal")


def _clean_people(df: pd.DataFrame, trail: AuditTrail, log) -> pd.DataFrame:
    """Clean names, work fields and entity types."""
    trail.run(
        df,
        _preclean_name_punctuation,
        "names_preclean_punctuation",
        "name_punctuation_cleaned",
        NAME_FIELDS,
    )

    # Reclassification restores raw work fields.
    raw_occupations = df["contributor_occupation"].copy()
    raw_employers = df["contributor_employer"].copy()
    df, occ_counts = clean_employer_occupation(df, trail)
    log(
        f"Occupations: {occ_counts['normalized']:,} normalized, "
        f"{occ_counts['occ_fixed']:,} typo-fixed, {occ_counts['comm_filled']:,} committees filled"
    )

    n_to_indiv, n_to_comm = trail.run(
        df, _reclassify_entities, "reclassify", _reclassify_reason,
        ENTITY_FIELDS + NAME_FIELDS + WORK_FIELDS,
    )
    log(
        f"Reclassified {n_to_indiv:,} committee -> INDIVIDUAL, {n_to_comm:,} individual -> COMMITTEE"
    )
    trail.run(
        df,
        lambda frame: _restore_reclassified_committees(
            frame, raw_occupations, raw_employers, log
        ),
        "reclassify_restore_work_fields",
        "raw_work_fields_restored_for_reclassified_committee", WORK_FIELDS,
    )
    trail.run(
        df, _clear_individual_residue, "reclassify_clear_residue",
        "short_junk_value_nulled_for_individual", WORK_FIELDS,
    )
    _clean_names(df, trail)
    return df


def _finish_records(df: pd.DataFrame, log):
    """Attach committee names and collect missing fields."""
    names = committee_id_to_name()
    df["recipient_committee"] = df["committee_id"].map(names).fillna(df["committee_id"])
    unknown_mask = df["committee_id"].notna() & ~df["committee_id"].isin(names)
    unknown = sorted(set(df.loc[unknown_mask, "committee_id"]))
    if unknown:
        log(f"WARNING: committee_id(s) not in committees.csv (kept raw): {unknown}")

    missing = _build_missing_report(df)

    columns = [column for column in OUTPUT_COLUMNS if column in df.columns]
    # Donor repairs still need this flag; output cleanup removes it.
    if "_care_of_blanked" in df.columns:
        columns.append("_care_of_blanked")
    return df[columns], missing


# Mark work from other filings or outside-source overrides as inferred; keep the rest filed.
def _employment_sources(df: pd.DataFrame, trail: AuditTrail, filed_work: pd.DataFrame) -> pd.Series:
    inferred = trail.keys_set_by(INFERRED_WORK_STEPS, ("contributor_employer", "contributor_occupation"))
    inferred |= outside_work_sub_ids(filed_work)
    has_work = (
        df["contributor_employer"].fillna("").astype(str).ne("")
        | df["contributor_occupation"].fillna("").astype(str).ne("")
    )
    from_others = df["sub_id"].astype(str).isin(inferred) & has_work
    return pd.Series("filed", index=df.index).mask(from_others, "inferred")
