"""Cleaning stages in execution order. Start with clean_pipeline."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.addresses.foreign import (
    restore_foreign_addresses,
    snapshot_foreign_addresses,
)
from fec.cleaning.entities import (
    _reclassify_entities,
    attach_source_fields,
)
from fec.cleaning.rules.manual import apply_manual_employer_overrides
from fec.cleaning.work.categorize import learn_categories
from fec.cleaning.work.read import first_reading
from fec.cleaning.work.status_words import extend_spellings
from fec.cleaning.work.typos import extend_corrections
from fec.cleaning.addresses.clean import clean_addresses
from fec.cleaning.donors.occupation import _rederive_occupation_category
from fec.cleaning.donors.clean import identify_donors, standardize as standardize_donors
from fec.cleaning.names import _clean_names, _preclean_name_punctuation
from fec.cleaning.rules.records import apply_record_rules
from fec.committees import committee_id_to_name
from fec.config.data import OUTPUT_COLUMNS
from fec.donor_match.normalize import extract_generational_suffix
from fec.log import get_logger

logger = get_logger(__name__)


# Clean records, align donor filings and write reports to out_dir.
def clean_pipeline(
    df: pd.DataFrame,
    out_dir: str | None = None,
    earlier_keys: dict | None = None,
):
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
    filed_employers, filed_occupations = df["contributor_employer"].copy(), df["contributor_occupation"].copy()
    df = _clean_people(df)
    logger.info("[2/5] Cleaning addresses")
    df = clean_addresses(df)
    df = _finish_records(df)

    logger.info("[3/5] Applying corrections")
    df = apply_record_rules(df, filed_employers, filed_occupations)

    # After the record rules: a company left in the occupation box has moved by now.
    _learn_categories(df)
    apply_manual_employer_overrides(df)
    # Donors are matched on the category of the occupation as it now reads.
    _rederive_occupation_category(df)
    return df


# Ask the model about occupations no table lists; log what it read.
def _learn_categories(df: pd.DataFrame) -> int:
    learnt = learn_categories(df)
    if learnt:
        logger.info("Occupation categories read from the model: %s", f"{learnt:,}")
    return learnt


def _prepare_records(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare IDs, states, dates and missing-work flags."""
    df["_generational_suffix"] = df["contributor_name"].map(
        extract_generational_suffix
    )
    dup_count = int(df["sub_id"].duplicated().sum())
    if dup_count:
        logger.warning("WARNING: Dropping %s duplicate sub_ids", f"{dup_count:,}")
        df = df.drop_duplicates(subset="sub_id", keep="first").copy()

    # Foreign and unknown states do not invalidate contributions.
    state = df["contributor_state"].astype("string").str.strip().str.upper()
    df["contributor_state"] = state.where(state.fillna("") != "", pd.NA).astype(object)

    df["contribution_receipt_date"] = pd.to_datetime(
        df["contribution_receipt_date"], errors="coerce"
    )

    warnings = _sanity_check(df)
    if warnings:
        logger.warning("WARNING: %s", '; '.join(warnings))
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


# Warn about future or unparseable dates.
def _sanity_check(df: pd.DataFrame) -> list[str]:
    warnings = []
    today = pd.Timestamp.now()
    n_future = int((df['contribution_receipt_date'] > today + pd.Timedelta(days=30)).sum())
    if n_future:
        warnings.append(f"future dates (>30 days ahead): {n_future:,}")

    n_null_date = int(df['contribution_receipt_date'].isna().sum())
    if n_null_date:
        warnings.append(f"unparseable dates: {n_null_date:,}")

    return warnings


def _clean_people(df: pd.DataFrame) -> pd.DataFrame:
    """Clean names, work fields and entity types."""
    _preclean_name_punctuation(df)

    first_reading(df)
    _reclassify_entities(df)
    _clean_names(df)
    return df


def _finish_records(df: pd.DataFrame):
    """Attach committee names."""
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
