"""Clean the raw FEC filings, match donors, check and save the cleaned CSV.

Run: python clean.py

Steps, in order (main):
    1. read_pipeline_csv            data/raw/contributions.csv
    2. clean_and_match
       a. clean_records             one record at a time:
          _prepare_records          drop duplicate IDs, read dates, attach FEC source fields
          _clean_people             names and entity types (classify_entities)
          clean_addresses           USPS form and reviewed address rules (cleaning/addresses)
          _finish_records           recipient committee names
          apply_record_rules        business names, then the work fields of each person (read_work)
          apply_filing_corrections  reviewed corrections by sub_id (data/rules/filing_corrections.csv)
       b. identify_donors           one donor key per person (fec/matching)
       c. standardize_donors        one name, job and address spelling per donor (fec/matching)
    3. _save_if_gates_pass          the quality checks, then data/output/contributions_cleaned.csv
    4. _save_donor_keys             data/cache/donor_keys.csv, so keys stay stable next month
    5. _write_new_occupations       data/reports/new_occupations.csv, texts not yet in the category table
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign_addresses import (
    foreign_address_mask,
    restore_foreign_addresses,
    snapshot_foreign_addresses,
)
from fec.cleaning.addresses.pipeline import clean_addresses
from fec.cleaning.contributor_names import (
    apply_name_corrections,
    clean_names,
    preclean_name_punctuation,
)
from fec.cleaning.entity_types import (
    attach_source_fields,
    classify_entities,
    normalize_business_names,
)
from fec.cleaning.filing_corrections import apply_filing_corrections
from fec.cleaning.quality_gates import run_quality_gates
from fec.cleaning.work.occupation_typos import extend_corrections
from fec.cleaning.work.status_spellings import extend_spellings
from fec.cleaning.work.text_cleanup import EMPLOYER_STATUS_TEXT, clean_box_text
from fec.cleaning.work.work_fields import read_work
from fec.common import settings as env
from fec.common.columns import CLEAN_COLUMNS, INTERNAL_OUTPUT_COLUMNS, OUTPUT_COLUMNS, check_output
from fec.common.committees import committee_id_to_name, read_committees
from fec.common.files import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV, DONOR_KEYS_CSV, RAW_CSV, REPORTS_DIR
from fec.matching.donor_work import recategorize_occupations
from fec.matching.name_compare import extract_generational_suffix
from fec.matching.pipeline import identify_donors, standardize_donors
from fec.vocabulary.occupations import EMPLOYER_NORMALIZE, OCCUPATION_CATEGORIES

logger = get_logger(__name__)


def clean_and_match(
    df: pd.DataFrame,
    out_dir: str | None = None,
    earlier_keys: dict | None = None,
) -> pd.DataFrame:
    """Clean records, align donor filings and write reports to out_dir."""
    df = df.copy()
    # Preserve filed foreign addresses before repairs.
    foreign = snapshot_foreign_addresses(df)

    df_clean = clean_records(df)
    logger.info("[4/5] Matching and standardizing donors")
    df_clean = identify_donors(df_clean, earlier_keys)
    df_clean = standardize_donors(df_clean, out_dir=out_dir)

    logger.info("[5/5] Checking data and saving")
    restore_foreign_addresses(df_clean, foreign)
    return df_clean


def clean_records(df: pd.DataFrame) -> pd.DataFrame:
    """Clean fields, apply record rules, then apply curated overrides."""
    logger.info("[1/5] Cleaning names and work")
    df = _prepare_records(df)
    filed_employers, filed_occupations = (
        df["contributor_employer"].copy(),
        df["contributor_occupation"].copy(),
    )
    df = _clean_people(df)
    logger.info("[2/5] Cleaning addresses")
    df = clean_addresses(df)
    df = _finish_records(df)

    logger.info("[3/5] Applying corrections")
    df = apply_record_rules(df, filed_employers, filed_occupations)

    apply_filing_corrections(df)
    # Donors are matched on the category of the occupation as it now reads.
    recategorize_occupations(df)
    return df


def _prepare_records(df: pd.DataFrame) -> pd.DataFrame:
    df["_generational_suffix"] = df["contributor_name"].map(extract_generational_suffix)
    dup_count = int(df["sub_id"].duplicated().sum())
    if dup_count:
        logger.warning("WARNING: Dropping %s duplicate sub_ids", f"{dup_count:,}")
        df = df.drop_duplicates(subset="sub_id", keep="first").copy()

    # Foreign and unknown states do not invalidate contributions.
    state = df["contributor_state"].astype("string").str.strip().str.upper()
    df["contributor_state"] = state.where(state.fillna("") != "", pd.NA).astype(object)

    df["contribution_receipt_date"] = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")

    warnings = _find_date_warnings(df)
    if warnings:
        logger.warning("WARNING: %s", "; ".join(warnings))
    raw = df["is_individual"].astype("string").fillna("").str.strip().str.lower()
    df["is_individual"] = raw.isin(("true", "t", "1", "yes"))
    attach_source_fields(df)
    learnt = extend_corrections(df)
    if learnt:
        logger.info("Occupation typos read from the filings: %s", f"{learnt:,}")
    slips = extend_spellings(df)
    if slips:
        logger.info("Status words read through a slip: %s", f"{slips:,}")
    return df


def _find_date_warnings(df: pd.DataFrame) -> list[str]:
    warnings = []
    today = pd.Timestamp.now()
    n_future = int((df["contribution_receipt_date"] > today + pd.Timedelta(days=30)).sum())
    if n_future:
        warnings.append(f"future dates (>30 days ahead): {n_future:,}")

    n_null_date = int(df["contribution_receipt_date"].isna().sum())
    if n_null_date:
        warnings.append(f"unparseable dates: {n_null_date:,}")

    return warnings


def _clean_people(df: pd.DataFrame) -> pd.DataFrame:
    preclean_name_punctuation(df)

    people = df["is_individual"].astype(bool)
    # The type is read from the employer as spelled; the work fields are read once the type is known.
    df.loc[people, "contributor_employer"] = clean_box_text(
        df.loc[people, "contributor_employer"], EMPLOYER_NORMALIZE, digits_only_before=EMPLOYER_STATUS_TEXT
    )
    df.loc[~people, ["contributor_occupation", "occupation_category"]] = [
        np.nan,
        "POLITICAL COMMITTEE",
    ]
    classify_entities(df)
    clean_names(df)
    apply_name_corrections(df)
    return df


def _finish_records(df: pd.DataFrame) -> pd.DataFrame:
    names = committee_id_to_name()
    df["recipient_committee"] = df["committee_id"].map(names).fillna(df["committee_id"])
    unknown_mask = df["committee_id"].notna() & ~df["committee_id"].isin(names)
    unknown = sorted(set(df.loc[unknown_mask, "committee_id"]))
    if unknown:
        logger.warning("WARNING: committee_id(s) not in committees.csv (kept raw): %s", unknown)

    columns = [column for column in OUTPUT_COLUMNS if column in df.columns]
    # Donor repairs still need this flag; output cleanup removes it.
    for column in ("previous_employer", "_retired_marker"):
        if column in df.columns and column not in columns:
            columns.append(column)
    return df[columns]


def apply_record_rules(df: pd.DataFrame, filed_employers: pd.Series, filed_occupations: pd.Series) -> pd.DataFrame:
    """Settle names, then read every individual's work fields from the filing."""
    df["is_individual"] = df["entity_type"].eq("INDIVIDUAL")
    normalize_business_names(df)
    read_work(df, df["is_individual"], filed_employers, filed_occupations)
    df.loc[
        df["entity_type"].eq("COMMITTEE/PAC") & df["occupation_category"].isna(),
        "occupation_category",
    ] = "POLITICAL COMMITTEE"
    return df


def main() -> pd.DataFrame:
    """Read the raw filings, clean them, run the checks and save the cleaned CSV."""
    start = time.perf_counter()
    env.load_env()

    input_path = str(RAW_CSV)
    output_path = str(CLEANED_CSV)
    out_dir = str(REPORTS_DIR)
    for folder in (out_dir, CLEANED_CSV.parent):
        os.makedirs(folder, exist_ok=True)

    df = read_pipeline_csv(input_path)
    logger.info("Reading: %s (%s rows)", os.path.relpath(input_path, env.PROJECT_ROOT), f"{len(df):,}")

    cleaned = clean_and_match(df, out_dir=out_dir, earlier_keys=_read_earlier_donor_keys())
    internal_columns = [
        column for column in cleaned.columns if column.startswith("_") or column in INTERNAL_OUTPUT_COLUMNS
    ]
    output = cleaned.drop(columns=internal_columns)

    _assert_known_committees(output)
    _ensure_zip_format(output)

    _save_if_gates_pass(output, out_dir, output_path)
    _save_donor_keys(cleaned)
    _write_new_occupations(output, out_dir)
    _print_summary(output, output_path, out_dir, time.perf_counter() - start)
    logger.info("Next: python geocode.py")
    return output


def _assert_known_committees(df: pd.DataFrame) -> None:
    known = {row["committee_short"] for row in read_committees() if row.get("committee_short")}
    recipients = df["recipient_committee"]
    unknown = recipients[recipients.notna() & ~recipients.isin(known)]
    if unknown.empty:
        return

    logger.error("  ABORT - %s rows have an unknown recipient:", f"{len(unknown):,}")
    for committee, count in unknown.value_counts().items():
        logger.error("    %-14s %s rows", committee, f"{count:,}")
    logger.error("  Add each recipient to data/reference/committees.csv.")
    raise SystemExit(1)


def _ensure_zip_format(df: pd.DataFrame) -> None:
    us_rows = ~foreign_address_mask(df)
    zips = df["contributor_zip"]

    if zips.dtype.name in ("object", "string"):
        valid = zips.isna() | zips.astype(str).str.match(r"^\d{5}$", na=False)
        if (valid | ~us_rows).all():
            return

    cleaned = (
        zips.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
        .str.replace(r"[^\d]", "", regex=True)
        .str.zfill(5)
        .str[:5]
    )
    invalid = cleaned.notna() & (~cleaned.str.match(r"^\d{5}$", na=False) | cleaned.eq("00000"))
    cleaned[invalid] = pd.NA
    df["contributor_zip"] = cleaned.where(us_rows, zips.astype("string"))


def _read_earlier_donor_keys() -> dict:
    if not DONOR_KEYS_CSV.exists():
        return {}
    keys = pd.read_csv(DONOR_KEYS_CSV, dtype=str, keep_default_na=False)
    return dict(zip(keys["sub_id"], keys["donor_key"]))


def _save_donor_keys(cleaned: pd.DataFrame) -> None:
    keys = cleaned[["sub_id", "_matched_key"]].rename(columns={"_matched_key": "donor_key"})
    write_csv_atomic(keys, str(DONOR_KEYS_CSV), index=False)


def _write_new_occupations(df: pd.DataFrame, out_dir: str) -> None:
    occupation = df["contributor_occupation"].fillna("")
    new = df[df["entity_type"].eq("INDIVIDUAL") & occupation.ne("") & ~occupation.isin(OCCUPATION_CATEGORIES)]
    report = new.groupby(["contributor_occupation", "occupation_category"]).size().reset_index(name="rows")
    write_csv_atomic(report, os.path.join(out_dir, "new_occupations.csv"), index=False)
    if len(report):
        logger.info("New occupations: %s (data/reports/new_occupations.csv)", f"{len(report):,}")


def _print_summary(df: pd.DataFrame, output: str, out_dir: str, elapsed: float) -> None:
    donors = df.loc[df["entity_type"].eq("INDIVIDUAL"), "donor_key"].nunique()
    minutes, seconds = divmod(round(elapsed), 60)
    logger.info("Clean checks passed.")
    logger.info("Rows: %s | Individual donors: %s", f"{len(df):,}", f"{donors:,}")
    logger.info("Saved: %s", os.path.relpath(output, env.PROJECT_ROOT))
    logger.info("Reports: %s", os.path.relpath(out_dir, env.PROJECT_ROOT))
    logger.info("Completed in %dm %02ds.", minutes, seconds)


def _save_if_gates_pass(output: pd.DataFrame, out_dir: str, output_path: str) -> None:
    """Save the CSV only after the applicable checks pass.

    Resolve reruns the checks that need employer_status.
    """
    quality = run_quality_gates(output)
    write_json_atomic(os.path.join(out_dir, "quality_gates.json"), {**quality, "stage": "clean"}, indent=2)
    if not quality["passed"]:
        logger.error("  ABORT - quality gates failed, %s kept as it was:", output_path)
        for issue in quality["issues"]:
            logger.error("    %s", issue)
        raise SystemExit(1)
    check_output("clean", (), output.columns)
    write_csv_atomic(output[list(CLEAN_COLUMNS)], output_path, index=False)
