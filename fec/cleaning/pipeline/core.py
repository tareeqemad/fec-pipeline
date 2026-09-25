"""Clean records, identify donors, and standardize their history."""

from __future__ import annotations

import time

import pandas as pd

from fec.cleaning.address_review import (
    build_review_queues,
    queue_counts,
    write_review_queues,
)
from fec.cleaning.audit_trail import (
    ADDRESS_FIELDS,
    ENTITY_FIELDS,
    NAME_FIELDS,
    WORK_FIELDS,
    AuditTrail,
)
from fec.cleaning.donor_consistency import INFERRED_WORK_STEPS
from fec.cleaning.foreign_addresses import (
    restore_foreign_addresses,
    snapshot_foreign_addresses,
)
from fec.cleaning.manual_overrides import apply_manual_employer_overrides
from fec.cleaning.occupations import clean_employer_occupation
from fec.cleaning.pipeline.donor_identity import identify_donors
from fec.cleaning.record_rules import apply_record_rules
from fec.committees import committee_id_to_name
from fec.config.data import MISSING_VALUES, OUTPUT_COLUMNS
from fec.config.geography import US_STATES
from fec.donor_match.normalize import extract_generational_suffix
from fec.log import get_logger

from .address_stage import clean_addresses, log_review_queues
from .donor_stage import standardize
from .name_parsing import _preclean_name_punctuation
from .names import _clean_names
from .reclassify import _reclassify_entities
from .reclassify_restore import (
    _clear_individual_residue,
    _restore_reclassified_committees,
)
from .reports import _build_missing_report, _sanity_check

logger = get_logger(__name__)


# dedupe, normalize state, mark missing occupation/employer, parse dates
def _prepare_records(df: pd.DataFrame, log) -> pd.DataFrame:
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
    # a filing is never dropped for its state: a Canadian 'ON' is a foreign
    # address the foreign step keeps as filed, and every donation counts
    unknown = int((~df["contributor_state"].isin(US_STATES)).sum())
    log(f"States: {unknown:,} filing(s) with a state outside the US list, kept as filed")

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


# default reclassification reason when none was recorded
def _reclassify_reason(df: pd.DataFrame) -> pd.Series:
    reasons = df["_reclass_reason"].astype(object)
    return reasons.where(reasons.notna(), "entity_type_from_business_or_committee_signal")


# clean names, occupations, and reclassify misclassified entity types
def _clean_people(df: pd.DataFrame, trail: AuditTrail, log) -> pd.DataFrame:
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


# attach committee names and build the missing-data report
def _finish_records(df: pd.DataFrame, log):
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
    return df[columns], missing


# clean contribution fields before donor matching
def _clean_fields(
    df: pd.DataFrame,
    trail: AuditTrail,
    out_dir: str | None = None,
    address_reports: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean contribution fields before donor matching."""
    start = time.time()
    log = logger.info

    df = _prepare_records(df, log)
    trail.start(df)
    df = _clean_people(df, trail, log)
    df = clean_addresses(df, trail, out_dir, log, reports=address_reports)
    df, missing = _finish_records(df, log)
    elapsed = time.time() - start
    log(f"Done: {len(df):,} rows in {elapsed:.1f}s")
    return df, missing


# clean every contribution record and reapply curated overrides
def clean_records(
    df: pd.DataFrame,
    trail: AuditTrail,
    out_dir: str | None = None,
    address_reports: dict | None = None,
):
    """Clean every contribution record.

    ``address_reports`` (optional) collects what the address stage leaves for the
    review queues, so the caller can write them later.
    """
    df_clean, missing = _clean_fields(
        df, trail, out_dir=out_dir, address_reports=address_reports,
    )

    logger.info("\n-- Record rules --")

    df_clean = apply_record_rules(df_clean, trail)

    # Reapply curated overrides.

    n_overrides = trail.run(
        df_clean, apply_manual_employer_overrides, "manual_overrides",
        "curated_row_override", WORK_FIELDS + ("previous_employer",
        "contributor_city", "contributor_street_1", "contributor_street_2", "contributor_zip"),
        source="data/manual_employer_overrides.csv",
    )
    if n_overrides:
        logger.info(f"  Manual employer overrides applied: {n_overrides:,} rows")

    return df_clean, missing


# make each donor consistent across filings
def standardize_donors(
    df_clean: pd.DataFrame,
    out_dir: str | None = None,
    trail: AuditTrail | None = None,
) -> pd.DataFrame:
    """Make each donor consistent across filings."""
    return standardize(df_clean, out_dir, trail or AuditTrail())


# write address review queues from the final cleaned rows
def _write_address_queues(df_clean, out_dir, address_reports: dict, foreign_sub_ids) -> None:
    """Write the address review queues from the final rows (read-only: edits nothing).

    Written here rather than in the address stage so donor-stage repairs are
    reflected and every row carries its donor_key; foreign filings (kept as
    filed, never geocoded) are not queued.
    """

    review_df, regeocode_df = build_review_queues(
        df_clean,
        address_reports.get("street2_auto_fixed"),
        exclude_sub_ids=foreign_sub_ids,
    )
    write_review_queues(out_dir, review_df, regeocode_df)
    log_review_queues(queue_counts(review_df, regeocode_df), logger.info)


# label each row's employer/occupation as filed or inferred
def _employment_sources(df: pd.DataFrame, trail: AuditTrail) -> pd.Series:
    """'inferred' where the employer or occupation came from other filings, else 'filed'."""
    inferred = trail.keys_set_by(INFERRED_WORK_STEPS, ("contributor_employer", "contributor_occupation"))
    has_work = (
        df["contributor_employer"].fillna("").astype(str).ne("")
        | df["contributor_occupation"].fillna("").astype(str).ne("")
    )
    from_others = df["sub_id"].astype(str).isin(inferred) & has_work
    return pd.Series("filed", index=df.index).mask(from_others, "inferred")


# run the complete cleaning pipeline end to end
def clean_pipeline(
    df: pd.DataFrame,
    out_dir: str | None = None,
):
    """Run the complete cleaning pipeline."""
    trail = AuditTrail()
    # foreign filings are kept exactly as filed: remember them before any repair
    foreign = snapshot_foreign_addresses(df)
    address_reports: dict = {}
    df_clean, missing = clean_records(
        df, trail, out_dir=out_dir, address_reports=address_reports,
    )
    df_clean = identify_donors(df_clean)
    df_clean = standardize_donors(df_clean, out_dir=out_dir, trail=trail)
    n_foreign = trail.run(
        df_clean, lambda frame: restore_foreign_addresses(frame, foreign),
        "foreign_address_restore", "foreign_address_kept_as_filed", ADDRESS_FIELDS,
    )
    logger.info(
        "  Foreign addresses: %s rows kept as filed (%s cells restored)",
        f"{len(foreign):,}", f"{n_foreign:,}",
    )
    if out_dir:
        _write_address_queues(df_clean, out_dir, address_reports, foreign.index)
    unexplained = trail.finish(df_clean)
    df_clean["employment_source"] = _employment_sources(df_clean, trail)
    if unexplained:
        logger.warning(
            "  Audit: %s field values changed outside tracked steps", f"{unexplained:,}"
        )
    return df_clean, missing, trail
