"""Resolve employer status and history, then write the cleaned CSV."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.cleaning.reports.gates import run_quality_gates
from fec.contract import check_input, check_output
from fec.env import CLEANED_CSV, PROJECT_ROOT, REPORTS_DIR, load_env
from fec.io import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger
from fec.resolve.apply import apply_results

logger = get_logger(__name__)

QUALITY_GATES_JSON = "quality_gates.json"


def _parse_args():
    parser = argparse.ArgumentParser(description="Resolve employer status and previous employers")
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    args = parser.parse_args()
    if not args.apply:
        parser.error("use --apply to write resolved data")
    return args


def _load_data(csv_path: str) -> pd.DataFrame:
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
    return df


def _write_quality_gates(quality: dict, csv_written: bool) -> None:
    """Write the quality report for the resolved data."""
    report = {**quality, "stage": "resolve", "csv_written": csv_written}
    write_json_atomic(Path(REPORTS_DIR) / QUALITY_GATES_JSON, report, indent=2)


def _write_results(df: pd.DataFrame, csv_path: str, input_columns) -> pd.DataFrame:
    df = apply_results(df)
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

    df = _load_data(csv_path)
    check_input("resolve", df.columns)
    input_columns = list(df.columns)
    logger.info("Reading: %s (%s rows)", os.path.relpath(csv_path, PROJECT_ROOT), f"{len(df):,}")

    df = _write_results(df, csv_path, input_columns)

    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Rows: %s", f"{len(df):,}")
    logger.info("Saved: %s", os.path.relpath(csv_path, PROJECT_ROOT))
    logger.info("Resolve checks passed. Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python loader.py --reset")
