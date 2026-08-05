"""CLI entry point for the resolve pipeline."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.env import load_env
from fec.log import get_logger

from .ai_client import (
    AIQuotaExhausted, ai_web_search_call, get_ai_client, get_ai_provider_model,
)
from .apply import apply_results
from .cache import Cache
from .constants import (
    AI_SYSTEM_PROMPT,
    COMMITTEE_CACHE,
    EMPLOYER_ADDR_CACHE,
    EMPLOYER_BRANCH_CACHE,
    PREV_EMPLOYER_CACHE,
)
from .dedup import dedup_by_resolved_address
from .helpers import _compute_donor_totals
from .manual_overrides import (
    load_manual_branches,
    load_manual_committee_overrides,
    load_manual_overrides,
)
from .stats import show_stats
from .steps.ai_employer import (
    EmployerLookup, _parse_ai_json, build_employer_prompt, step_ai_lookup,
)
from .steps.committee_address import step_committees_own_address
from .steps.previous_employer import step_cross_record, step_fec_api

logger = get_logger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve employer/committee addresses"
    )
    parser.add_argument("input", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Count without API calls")
    parser.add_argument("--stats", action="store_true", help="Show status")
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    parser.add_argument(
        "--test-ai", action="store_true",
        help="Test the AI provider API key and exit",
    )
    return parser.parse_args()


def _test_ai_provider() -> bool:
    try:
        client, model, provider = get_ai_client()
    except ImportError:
        logger.info("openai package not installed - run: pip install openai")
        return False
    if client is None:
        return False

    logger.info(f"  Provider: {provider}")
    logger.info(f"  Model: {model}")
    logger.info("  Testing: 'GOLDMAN SACHS'...")
    try:
        text, cost = ai_web_search_call(
            client,
            model,
            AI_SYSTEM_PROMPT,
            build_employer_prompt(EmployerLookup("GOLDMAN SACHS")),
        )
        logger.info("  Response:")
        for result in _parse_ai_json(text):
            if not isinstance(result, dict):
                continue
            logger.info(
                f"    {result['name']}: {result.get('address', '?')}, "
                f"{result.get('city', '?')}, {result.get('state', '?')} "
                f"[{result.get('confidence', '?')}]"
            )
        logger.info(f"  Search cost: ~${cost:.4f}")
        return True
    except Exception as error:
        logger.error(f" Error: {error}")
        return False


def _find_csv_path(requested: str | None) -> str | None:
    if requested:
        return requested if os.path.exists(requested) else None
    default = "data/contributions_cleaned.csv"
    return default if os.path.exists(default) else None


def _deduplicate_employers(df: pd.DataFrame, addr_cache) -> None:
    logger.info("\n-- Step 3b: Same-address dedup --")
    frequencies = df["contributor_employer"].value_counts().to_dict()
    removed, groups, mapping = dedup_by_resolved_address(
        addr_cache, freq=frequencies
    )
    logger.info(f"    merged {removed:,} cache entries across {groups:,} groups")
    if not mapping:
        return

    employers = df["contributor_employer"]
    to_remap = employers.isin(mapping)
    rows_changed = int(to_remap.sum())
    if rows_changed:
        df.loc[to_remap, "contributor_employer"] = employers[to_remap].map(mapping)
        logger.info(f"    rewrote {rows_changed:,} CSV rows to canonical names")


def _write_results(
    df: pd.DataFrame,
    csv_path: str,
    prev_cache,
    addr_cache,
    comm_cache,
    branch_cache,
) -> pd.DataFrame:
    logger.info(f"\n-- Writing results -> {csv_path} --")
    df = apply_results(
        df, prev_cache, addr_cache, comm_cache, branch_cache
    )

    # Expand Saint/Mount/Fort in resolved employer cities, matching clean.py.
    from fec.config import expand_city_abbreviations
    df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

    destination = Path(csv_path)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    df.to_csv(temporary, index=False)
    os.replace(temporary, destination)
    logger.info(f"  Written {len(df):,} rows")
    return df


def main() -> None:
    args = _parse_args()
    load_env()

    if args.test_ai:
        if not _test_ai_provider():
            sys.exit(1)
        return

    csv_path = _find_csv_path(args.input)
    if not csv_path:
        logger.info("Error: no cleaned CSV found")
        sys.exit(1)

    data_dir = os.path.dirname(csv_path) or "."
    prev_cache = Cache(os.path.join(data_dir, PREV_EMPLOYER_CACHE))
    addr_cache = Cache(os.path.join(data_dir, EMPLOYER_ADDR_CACHE))
    comm_cache = Cache(os.path.join(data_dir, COMMITTEE_CACHE))
    branch_cache = Cache(os.path.join(data_dir, EMPLOYER_BRANCH_CACHE))

    from fec.io import read_pipeline_csv
    df = read_pipeline_csv(csv_path)
    df["contribution_receipt_amount"] = pd.to_numeric(
        df["contribution_receipt_amount"], errors="coerce"
    ).fillna(0)
    donor_totals = _compute_donor_totals(df)

    if args.stats:
        show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)
        return

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Resolve - Employer & Committee Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"{'=' * 60}")
    total_start = time.time()

    logger.info("\n-- Step 0: Manual overrides --")
    manual_path = Path(data_dir) / "manual_employer_addresses.csv"
    load_manual_overrides(manual_path, addr_cache)
    load_manual_branches(manual_path, branch_cache)
    load_manual_committee_overrides(
        Path(data_dir) / "manual_committee_addresses.csv", comm_cache
    )

    logger.info("\n-- Step 1: Cross-Record (find previous employer from our data) --")
    step_cross_record(df, prev_cache)

    logger.info("\n-- Step 2: FEC API (find previous employer from other committees) --")
    step_fec_api(df, prev_cache, donor_totals, dry_run=args.dry_run)

    provider, model = get_ai_provider_model()
    logger.info(
        f"\n-- Step 3: AI Lookup ({provider} {model} - employer HQ addresses) --"
    )
    ai_incomplete = False
    try:
        step_ai_lookup(
            df, prev_cache, addr_cache, donor_totals, dry_run=args.dry_run
        )
    except AIQuotaExhausted as error:
        ai_incomplete = True
        logger.error(f"\n  STOPPED: {error}")
        logger.warning(
            "  Continuing with cached addresses; unresolved employers stay blank."
        )

    if not args.dry_run:
        _deduplicate_employers(df, addr_cache)

    logger.info("\n-- Step 4: Committee Addresses (from FEC filings) --")
    step_committees_own_address(df, comm_cache)

    if args.apply and not args.dry_run:
        df = _write_results(
            df, csv_path, prev_cache, addr_cache, comm_cache, branch_cache
        )
    elif not args.dry_run:
        logger.info(
            f"\n  Caches updated - the CSV was NOT modified.\n"
            f"    Re-run with --apply to write employer_address/city/state onto\n"
            f"    {csv_path} (needed before `geocode.py --employer-only`)."
        )

    minutes, seconds = divmod(int(time.time() - total_start), 60)
    logger.info(f"\n  Time: {minutes}m {seconds}s")
    show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)

    if ai_incomplete:
        if args.apply and not args.dry_run:
            logger.warning(
                "\n  PARTIAL: cached addresses were written, but AI resolution is incomplete."
            )
        logger.warning(
            "  Add credits and rerun the same command to fill the remaining employers."
        )
        sys.exit(2)
