"""CLI entry point for the resolve pipeline."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.env import CLEANED_CSV, load_env
from fec.log import get_logger

from .ai_client import AIQuotaExhausted, get_ai_provider_model
from .apply import apply_results
from .cache import Cache
from .constants import COMMITTEE_CACHE, EMPLOYER_ADDR_CACHE, PREV_EMPLOYER_CACHE
from .dedup import dedup_by_resolved_address
from .helpers import _compute_donor_totals
from .manual_overrides import (
    load_manual_committee_overrides,
    load_manual_locations,
    load_manual_previous_employers,
)
from .stats import show_stats
from .steps.ai_employer import step_ai_lookup
from .steps.committee_address import step_committees_own_address
from .steps.previous_employer import (
    migrate_previous_employer_cache,
    step_cross_record,
    step_fec_api,
)

logger = get_logger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve employer/committee addresses"
    )
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    args = parser.parse_args()
    if not args.apply:
        parser.error("use --apply to write resolved data")
    return args


def _load_data(csv_path: str):
    from fec.io import read_pipeline_csv

    data_dir = os.path.dirname(csv_path) or "."
    previous = Cache(os.path.join(data_dir, PREV_EMPLOYER_CACHE))
    employers = Cache(os.path.join(data_dir, EMPLOYER_ADDR_CACHE))
    committees = Cache(os.path.join(data_dir, COMMITTEE_CACHE))

    df = read_pipeline_csv(csv_path)
    df["contribution_receipt_amount"] = pd.to_numeric(
        df["contribution_receipt_amount"], errors="coerce"
    ).fillna(0)
    totals = _compute_donor_totals(df)
    return data_dir, df, totals, previous, employers, committees


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
    comm_cache,
) -> bool:
    """Run the resolve stages; return whether AI stopped early."""
    migrate_previous_employer_cache(df, prev_cache)

    logger.info("\n-- Step 0: Manual overrides --")
    load_manual_locations(
        Path(data_dir) / "manual_employer_addresses.csv", addr_cache
    )
    load_manual_previous_employers(
        Path(data_dir) / "manual_employer_overrides.csv", df, prev_cache,
    )
    load_manual_committee_overrides(
        Path(data_dir) / "manual_committee_addresses.csv", comm_cache
    )

    logger.info("\n-- Step 1: Cross-record previous employers --")
    step_cross_record(df, prev_cache)

    logger.info("\n-- Step 2: FEC previous employers --")
    step_fec_api(df, prev_cache, donor_totals)

    provider, model = get_ai_provider_model()
    logger.info(f"\n-- Step 3: Employer locations ({provider} {model}) --")
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

    logger.info("\n-- Step 4: Committee addresses --")
    step_committees_own_address(df, comm_cache)
    return ai_incomplete


def _write_results(
    df: pd.DataFrame,
    csv_path: str,
    prev_cache,
    addr_cache,
    comm_cache,
) -> pd.DataFrame:
    logger.info(f"\n-- Writing results -> {csv_path} --")
    df = apply_results(df, prev_cache, addr_cache, comm_cache)

    from fec.cleaning.quality import run_quality_gates
    quality = run_quality_gates(df)
    if not quality["passed"]:
        details = "; ".join(quality["issues"]) or "quality gate failed"
        raise ValueError(f"Resolved CSV was not written: {details}")

    # Expand Saint/Mount/Fort in resolved employer cities, matching clean.py.
    from fec.config.cities import expand_city_abbreviations
    df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

    destination = Path(csv_path)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    df.to_csv(temporary, index=False)
    os.replace(temporary, destination)
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
    data_dir, df, donor_totals, prev_cache, addr_cache, comm_cache = data

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Resolve - Employer & Committee Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"{'=' * 60}")
    total_start = time.time()

    ai_incomplete = _run_steps(
        df,
        donor_totals,
        data_dir,
        prev_cache,
        addr_cache,
        comm_cache,
    )

    df = _write_results(
        df, csv_path, prev_cache, addr_cache, comm_cache
    )

    minutes, seconds = divmod(int(time.time() - total_start), 60)
    logger.info(f"\n  Time: {minutes}m {seconds}s")
    show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)

    if ai_incomplete:
        logger.warning(
            "\n  PARTIAL: cached addresses were written, but AI resolution is incomplete."
        )
        logger.warning(
            "  Add credits and rerun the same command to fill the remaining employers."
        )
        sys.exit(2)
