"""Resolve each filing's employer status and previous employer, then write the cleaned CSV.

Run: python resolve.py --apply

Steps, in order (main):
    1. read_pipeline_csv       data/output/contributions_cleaned.csv
    2. resolve_employers
       a. classify_employer_statuses   active, retired, self-employed, not employed, missing
       b. previous_employers           for donors whose latest filing says retired (previous_employer.py)
    3. run_quality_gates       the checks the data must pass; a failure stops before the CSV is written
    4. write_csv_atomic        the CSV, then data/reports/quality_gates.json
"""

import argparse
import sys
import time

import pandas as pd

from fec.cleaning.quality_gates import run_quality_gates
from fec.cleaning.work.employer_status import classify_employer_statuses
from fec.common.columns import check_input, check_output
from fec.common.files import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV, PROJECT_ROOT, REPORTS_DIR
from fec.resolve.previous_employer import previous_employers

logger = get_logger(__name__)

QUALITY_GATES_JSON = REPORTS_DIR / "quality_gates.json"


def resolve_employers(df: pd.DataFrame) -> pd.DataFrame:
    """Set each filing's employer status and its donor's previous employer."""
    status = classify_employer_statuses(df)
    status[df["entity_type"].eq("COMMITTEE/PAC")] = "committee"
    status[df["entity_type"].eq("ORGANIZATION")] = "organization"
    df["employer_status"] = status
    df["previous_employer"] = previous_employers(df, status)
    return df


def main() -> None:
    """Read the cleaned CSV, resolve employer status and history, check and save it."""
    start = time.perf_counter()
    parser = argparse.ArgumentParser(description="Resolve employer status and previous employers")
    parser.add_argument("--apply", action="store_true", required=True, help="Write resolve columns to CSV")
    parser.parse_args()
    if not CLEANED_CSV.exists():
        logger.error("Error: no cleaned CSV found; run python clean.py first")
        sys.exit(1)
    csv_name = CLEANED_CSV.relative_to(PROJECT_ROOT)
    df = read_pipeline_csv(CLEANED_CSV)
    check_input("resolve.py --apply", df.columns, "employer_status")
    input_columns = list(df.columns)
    logger.info("Reading: %s (%s rows)", csv_name, f"{len(df):,}")

    df = resolve_employers(df)
    quality = run_quality_gates(df)
    report = {**quality, "stage": "resolve", "csv_written": False}
    if not quality["passed"]:
        write_json_atomic(QUALITY_GATES_JSON, report, indent=2)
        raise ValueError(f"Resolved CSV was not written: {'; '.join(quality['issues']) or 'quality gate failed'}")
    check_output("resolve.py --apply", df.columns, [*input_columns, "employer_status"])
    write_csv_atomic(df, CLEANED_CSV, index=False)
    write_json_atomic(QUALITY_GATES_JSON, {**report, "csv_written": True}, indent=2)

    minutes, seconds = divmod(round(time.perf_counter() - start), 60)
    logger.info("Rows: %s", f"{len(df):,}")
    logger.info("Saved: %s", csv_name)
    logger.info("Resolve checks passed. Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python loader.py --reset")
