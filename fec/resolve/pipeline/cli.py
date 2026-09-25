"""CLI entry point for the resolve pipeline."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.cleaning.quality import run_quality_gates
from fec.config.cities import expand_city_abbreviations
from fec.env import CLEANED_CSV, load_env
from fec.io import read_pipeline_csv
from fec.log import get_logger
from fec.resolve.pipeline.steps.fec_previous_employer import step_fec_api

from .ai_client import PROVIDER, AIQuotaExhausted, get_ai_model
from .apply import apply_results
from .cache import Cache
from .constants import EMPLOYER_ADDR_CACHE, PREV_EMPLOYER_CACHE
from .dedup import dedup_by_resolved_address
from .helpers import _compute_donor_totals
from .manual_overrides import (
    load_manual_locations,
    load_manual_previous_employers,
)
from .stats import show_stats
from .steps.ai_employer import step_ai_lookup
from .steps.previous_employer import step_cross_record

logger = get_logger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve employer addresses"
    )
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    args = parser.parse_args()
    if not args.apply:
        parser.error("use --apply to write resolved data")
    return args


def _load_data(csv_path: str):
    data_dir = os.path.dirname(csv_path) or "."
    previous = Cache(os.path.join(data_dir, PREV_EMPLOYER_CACHE))
    employers = Cache(os.path.join(data_dir, EMPLOYER_ADDR_CACHE))

    df = read_pipeline_csv(csv_path)
    amounts = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce")
    unreadable = amounts.isna()
    if unreadable.any():
        # a missing amount is fixed in cleaning; writing it back as $0 would hide it
        examples = ", ".join(df.loc[unreadable, "sub_id"].astype(str).head(5))
        raise ValueError(
            f"{int(unreadable.sum()):,} row(s) with a missing or unreadable "
            f"contribution_receipt_amount (sub_id {examples})"
        )
    df["contribution_receipt_amount"] = amounts
    totals = _compute_donor_totals(df)
    return data_dir, df, totals, previous, employers


def _deduplicate_address_cache(df: pd.DataFrame, addr_cache) -> None:
    logger.info("\n-- Step 3b: Address-cache aliases --")
    frequencies = df["contributor_employer"].value_counts().to_dict()
    aliases, groups = dedup_by_resolved_address(
        addr_cache, freq=frequencies
    )
    logger.info(f"    created {aliases:,} aliases across {groups:,} groups")


def _run_steps(
    df,
    donor_totals,
    data_dir,
    prev_cache,
    addr_cache,
) -> bool:
    """Run the resolve stages; return whether AI stopped early."""
    logger.info("\n-- Step 0: Manual overrides --")
    load_manual_locations(
        Path(data_dir) / "manual_employer_addresses.csv", addr_cache
    )
    load_manual_previous_employers(
        Path(data_dir) / "manual_employer_overrides.csv", df, prev_cache,
    )

    logger.info("\n-- Step 1: Cross-record previous employers --")
    step_cross_record(df, prev_cache)

    logger.info("\n-- Step 2: FEC previous employers --")
    step_fec_api(df, prev_cache, donor_totals)

    logger.info(f"\n-- Step 3: Employer locations ({PROVIDER} {get_ai_model()}) --")
    ai_incomplete = False
    try:
        step_ai_lookup(
            df,
            prev_cache,
            addr_cache,
            donor_totals,
        )
    except AIQuotaExhausted as error:
        ai_incomplete = True
        logger.error(f"\n  STOPPED: {error}")
        logger.warning(
            "  Continuing with cached addresses; unresolved employers stay blank."
        )

    _deduplicate_address_cache(df, addr_cache)
    return ai_incomplete


QUALITY_GATES_JSON = "quality_gates.json"


def _write_quality_gates(quality: dict, data_dir: str, csv_written: bool) -> None:
    """Replace clean.py's gate report with the one run on the resolved data.

    clean.py writes the report before resolve adds employer_status, so four
    gates read not_run there; this is the report of the final file.
    """
    report = {**quality, "stage": "resolve", "csv_written": csv_written}
    destination = Path(data_dir) / QUALITY_GATES_JSON
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    os.replace(temporary, destination)


def _write_results(
    df: pd.DataFrame,
    csv_path: str,
    prev_cache,
    addr_cache,
) -> pd.DataFrame:
    logger.info(f"\n-- Writing results -> {csv_path} --")
    df = apply_results(df, prev_cache, addr_cache)
    data_dir = os.path.dirname(csv_path) or "."

    quality = run_quality_gates(df)
    if not quality["passed"]:
        _write_quality_gates(quality, data_dir, csv_written=False)
        details = "; ".join(quality["issues"]) or "quality gate failed"
        raise ValueError(f"Resolved CSV was not written: {details}")

    # Expand Saint/Mount/Fort in resolved employer cities, matching clean.py.
    df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

    destination = Path(csv_path)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    df.to_csv(temporary, index=False)
    os.replace(temporary, destination)
    # the gates read no employer_city, so the report above describes this file
    _write_quality_gates(quality, data_dir, csv_written=True)
    logger.info(f"  Written {len(df):,} rows")
    return df


def main() -> None:
    _parse_args()
    load_env()

    csv_path = str(CLEANED_CSV)
    if not CLEANED_CSV.exists():
        logger.info("Error: no cleaned CSV found")
        sys.exit(1)

    data = _load_data(csv_path)
    data_dir, df, donor_totals, prev_cache, addr_cache = data

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Resolve - Employer Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"{'=' * 60}")
    total_start = time.time()

    ai_incomplete = _run_steps(
        df,
        donor_totals,
        data_dir,
        prev_cache,
        addr_cache,
    )

    df = _write_results(df, csv_path, prev_cache, addr_cache)

    minutes, seconds = divmod(int(time.time() - total_start), 60)
    logger.info(f"\n  Time: {minutes}m {seconds}s")
    show_stats(df, prev_cache, addr_cache, donor_totals)

    if ai_incomplete:
        logger.warning(
            "\n  PARTIAL: cached addresses were written, but AI resolution is incomplete."
        )
        logger.warning(
            "  Add credits and rerun the same command to fill the remaining employers."
        )
        sys.exit(2)
