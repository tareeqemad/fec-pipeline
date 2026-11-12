"""Clean the raw FEC filings, match donors, check and save the cleaned CSV.

Run: python clean.py

Steps, in order (main):
    1. read_pipeline_csv            data/raw/contributions.csv
    2. clean_and_match              the filed foreign addresses are kept aside, then put back at the end
       a. clean_records             one record at a time:
          _prepare_records          drop duplicate IDs, read dates, attach FEC source fields
          _clean_people             names and entity types (classify_entities)
          clean_addresses           USPS form and reviewed address rules (cleaning/addresses)
          recipient_committee       committee names; only the cleaned and working columns stay
          read_work                 business names, then the work fields of each person
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

from fec.cleaning.addresses.foreign_addresses import restore_foreign_addresses, snapshot_foreign_addresses
from fec.cleaning.addresses.pipeline import clean_addresses
from fec.cleaning.contributor_names import apply_name_corrections, clean_names, preclean_name_punctuation
from fec.cleaning.entity_types import SOURCE_COLUMNS, attach_source_fields, classify_entities, normalize_business_names
from fec.cleaning.filing_corrections import apply_filing_corrections
from fec.cleaning.quality_gates import run_quality_gates
from fec.cleaning.work.occupation_typos import extend_corrections
from fec.cleaning.work.status_spellings import extend_spellings
from fec.cleaning.work.text_cleanup import clean_box_text
from fec.cleaning.work.work_fields import read_work
from fec.common import settings as env
from fec.common.columns import CLEAN_COLUMNS
from fec.common.committees import committee_id_to_name, read_committees
from fec.common.files import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV, DONOR_KEYS_CSV, RAW_CSV, REPORTS_DIR
from fec.matching.donor_work import recategorize_occupations
from fec.matching.name_compare import extract_generational_suffix
from fec.matching.pipeline import identify_donors, standardize_donors
from fec.vocabulary.occupations import OCCUPATION_CATEGORIES

logger = get_logger(__name__)

# Read while cleaning and matching, never written: FEC's own type and id, and what the filed name and work said.
_WORKING_COLUMNS = (*SOURCE_COLUMNS, "_generational_suffix", "_retired_marker")


def clean_and_match(df: pd.DataFrame, earlier_keys: dict | None = None) -> pd.DataFrame:
    """Clean records and match donors; foreign addresses end as filed."""
    df = df.copy()
    foreign = snapshot_foreign_addresses(df)
    df_clean = clean_records(df)
    logger.info("[4/5] Matching and standardizing donors")
    df_clean = identify_donors(df_clean, earlier_keys)
    df_clean = standardize_donors(df_clean)
    logger.info("[5/5] Checking data and saving")
    restore_foreign_addresses(df_clean, foreign)
    return df_clean


def clean_records(df: pd.DataFrame) -> pd.DataFrame:
    """Clean fields, apply record rules, then apply curated overrides."""
    logger.info("[1/5] Cleaning names and work")
    df = _prepare_records(df)
    filed_employers, filed_occupations = df["contributor_employer"].copy(), df["contributor_occupation"].copy()
    df = _clean_people(df)
    logger.info("[2/5] Cleaning addresses")
    df = clean_addresses(df)
    df["recipient_committee"] = df["committee_id"].map(committee_id_to_name()).fillna(df["committee_id"])
    # The cleaned columns, and the working columns donor matching still reads.
    df = df[[column for column in df.columns if column in CLEAN_COLUMNS or column in _WORKING_COLUMNS]]
    logger.info("[3/5] Applying corrections")
    df["is_individual"] = df["entity_type"].eq("INDIVIDUAL")
    normalize_business_names(df)
    read_work(df, df["is_individual"], filed_employers, filed_occupations)
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
    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    df["contribution_receipt_date"] = dates
    n_future, n_unread = int((dates > pd.Timestamp.now() + pd.Timedelta(days=30)).sum()), int(dates.isna().sum())
    if n_future or n_unread:
        logger.warning("WARNING: future dates (>30 days ahead): %s; unparseable dates: %s", n_future, n_unread)
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


def _clean_people(df: pd.DataFrame) -> pd.DataFrame:
    preclean_name_punctuation(df)
    people = df["is_individual"].astype(bool)
    # The type is read from the employer as spelled; the work fields are read once the type is known.
    df.loc[people, "contributor_employer"] = clean_box_text(df.loc[people, "contributor_employer"], employer=True)
    df.loc[~people, ["contributor_occupation", "occupation_category"]] = [np.nan, "POLITICAL COMMITTEE"]
    classify_entities(df)
    clean_names(df)
    apply_name_corrections(df)
    return df


def main() -> pd.DataFrame:
    """Read the raw filings, clean them, run the checks and save the cleaned CSV."""
    start = time.perf_counter()
    env.load_env()
    out_dir = str(REPORTS_DIR)
    df = read_pipeline_csv(RAW_CSV)
    logger.info("Reading: %s (%s rows)", os.path.relpath(RAW_CSV, env.PROJECT_ROOT), f"{len(df):,}")
    cleaned = clean_and_match(df, earlier_keys=_read_earlier_donor_keys())
    output = cleaned[list(CLEAN_COLUMNS)].copy()
    _assert_known_committees(output)
    _save_if_gates_pass(output, out_dir)
    _save_donor_keys(cleaned)
    _write_new_occupations(output, out_dir)
    _print_summary(output, out_dir, time.perf_counter() - start)
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


def _print_summary(df: pd.DataFrame, out_dir: str, elapsed: float) -> None:
    donors = df.loc[df["entity_type"].eq("INDIVIDUAL"), "donor_key"].nunique()
    minutes, seconds = divmod(round(elapsed), 60)
    logger.info("Clean checks passed.")
    logger.info("Rows: %s | Individual donors: %s", f"{len(df):,}", f"{donors:,}")
    logger.info("Saved: %s", os.path.relpath(CLEANED_CSV, env.PROJECT_ROOT))
    logger.info("Reports: %s", os.path.relpath(out_dir, env.PROJECT_ROOT))
    logger.info("Completed in %dm %02ds.", minutes, seconds)


def _save_if_gates_pass(output: pd.DataFrame, out_dir: str) -> None:
    # Resolve reruns the checks that need employer_status.
    quality = run_quality_gates(output)
    write_json_atomic(os.path.join(out_dir, "quality_gates.json"), {**quality, "stage": "clean"}, indent=2)
    if not quality["passed"]:
        logger.error("  ABORT - quality gates failed, %s kept as it was:", CLEANED_CSV)
        for issue in quality["issues"]:
            logger.error("    %s", issue)
        raise SystemExit(1)
    write_csv_atomic(output, str(CLEANED_CSV), index=False)
