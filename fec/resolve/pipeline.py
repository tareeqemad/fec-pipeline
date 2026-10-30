"""Resolve employer status and history, then write the cleaned CSV."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.cleaning.reports.gates import run_quality_gates
from fec.contract import check_input, check_output
from fec.env import (
    CACHE_DIR, CLEANED_CSV, MANUAL_EMPLOYER_OVERRIDES_CSV, PROJECT_ROOT, RAW_CSV, REPORTS_DIR, load_env,
)
from fec.io import JsonCache, read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger
from fec.resolve.apply import apply_results
from fec.resolve.cache import PREV_EMPLOYER_CACHE
from fec.resolve.fec import drop_unproven_source_entries
from fec.resolve.manual import cleared_employers, load_manual_previous_employers
from fec.resolve.previous import step_cross_record

logger = get_logger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve employer status and previous employers"
    )
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    args = parser.parse_args()
    if not args.apply:
        parser.error("use --apply to write resolved data")
    return args


def _load_data(csv_path: str):
    previous = JsonCache(os.path.join(CACHE_DIR, PREV_EMPLOYER_CACHE))

    df = read_pipeline_csv(csv_path)
    amounts = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce")
    unreadable = amounts.isna()
    if unreadable.any():
        # Cleaning must fix missing amounts; writing $0 would hide them.
        examples = ", ".join(df.loc[unreadable, "sub_id"].astype(str).head(5))
        raise ValueError(
            f"{int(unreadable.sum()):,} row(s) with a missing or unreadable "
            f"contribution_receipt_amount (sub_id {examples})"
        )
    df["contribution_receipt_amount"] = amounts
    return df, previous


def _run_steps(df, prev_cache) -> None:
    """Apply reviewed corrections and cached work history."""
    logger.info("[1/2] Applying reviewed corrections")
    load_manual_previous_employers(MANUAL_EMPLOYER_OVERRIDES_CSV, df, prev_cache)
    unverified = drop_unproven_source_entries(prev_cache, df, cleared_employers(df))
    if unverified:
        prev_cache.save()
        logger.warning("WARNING: Withheld %s FEC answers without matching source evidence", f"{unverified:,}")

    logger.info("[2/2] Checking donor work history")
    step_cross_record(df, prev_cache)


QUALITY_GATES_JSON = "quality_gates.json"


def _write_quality_gates(quality: dict, csv_written: bool) -> None:
    """Write the quality report for the resolved data."""
    report = {**quality, "stage": "resolve", "csv_written": csv_written}
    write_json_atomic(Path(REPORTS_DIR) / QUALITY_GATES_JSON, report, indent=2)


def _filed_employers() -> dict:
    """sub_id to raw employer text; empty without the raw file."""
    if not RAW_CSV.exists():
        return {}
    raw = pd.read_csv(RAW_CSV, dtype=str, keep_default_na=False, usecols=["sub_id", "contributor_employer"])
    return dict(zip(raw["sub_id"].str.strip(), raw["contributor_employer"]))


def _write_results(
    df: pd.DataFrame,
    csv_path: str,
    prev_cache,
    input_columns,
) -> pd.DataFrame:
    logger.info("[5/5] Checking data and saving")
    df = apply_results(df, prev_cache, _filed_employers())
    quality = run_quality_gates(df)
    if not quality["passed"]:
        _write_quality_gates(quality, csv_written=False)
        details = "; ".join(quality["issues"]) or "quality gate failed"
        raise ValueError(f"Resolved CSV was not written: {details}")


    check_output("resolve", input_columns, df.columns)
    write_csv_atomic(df, csv_path, index=False)
    _write_quality_gates(quality, csv_written=True)
    return df


def main() -> None:
    total_start = time.perf_counter()
    _parse_args()
    load_env()

    csv_path = str(CLEANED_CSV)
    if not CLEANED_CSV.exists():
        logger.error("Error: no cleaned CSV found; run python clean.py first")
        sys.exit(1)

    df, prev_cache = _load_data(csv_path)
    check_input("resolve", df.columns)
    input_columns = list(df.columns)

    logger.info("Reading: %s (%s rows)", os.path.relpath(csv_path, PROJECT_ROOT), f"{len(df):,}")

    _run_steps(df, prev_cache)

    df = _write_results(df, csv_path, prev_cache, input_columns)

    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Rows: %s", f"{len(df):,}")
    logger.info("Saved: %s", os.path.relpath(csv_path, PROJECT_ROOT))

    logger.info("Resolve checks passed. Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python loader.py --reset")
