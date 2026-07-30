"""CLI entry point for the resolve pipeline."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.log import get_logger

from .constants import (
    TIERS, EMPLOYER_ADDR_CACHE, PREV_EMPLOYER_CACHE, COMMITTEE_CACHE,
    EMPLOYER_BRANCH_CACHE, AI_SYSTEM_PROMPT,
)
from .cache import Cache
from .ai_client import get_ai_client, get_ai_provider_model, ai_json_call
from .helpers import _load_env, _compute_donor_totals
from .steps.cross_record import step_cross_record
from .steps.fec_api import step_fec_api
from .steps.ai_employer import step_ai_lookup, _parse_ai_json
from .steps.committee_address import step_committees_own_address
from .apply import apply_results
from .dedup import dedup_by_resolved_address
from .manual_overrides import (
    load_manual_overrides, load_manual_committee_overrides, load_manual_branches,
)
from .stats import show_stats

logger = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve employer/committee addresses")
    parser.add_argument("input", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true", help="Count without API calls")
    parser.add_argument("--stats", action="store_true", help="Show status")
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    parser.add_argument("--test-ai", action="store_true", help="Test the AI provider API key and exit")
    args = parser.parse_args()

    _load_env()

    if args.test_ai:
        try:
            client, model, provider = get_ai_client()
        except ImportError:
            logger.info("openai package not installed - run: pip install openai")
            sys.exit(1)
        if client is None:
            sys.exit(1)
        logger.info(f"  Provider: {provider}")
        logger.info(f"  Model: {model}")
        logger.info("  Testing: 'GOLDMAN SACHS, GOOGLE'...")
        try:
            text = ai_json_call(
                client, model, provider,
                AI_SYSTEM_PROMPT, '1. "GOLDMAN SACHS"\n2. "GOOGLE"',
                max_tokens=2000,
            )
            results = _parse_ai_json(text)
            logger.info("  Response:")
            for result in results:
                if not isinstance(result, dict):
                    continue
                logger.info(f"    {result['name']}: {result.get('address','?')}, {result.get('city','?')}, {result.get('state','?')} [{result.get('confidence','?')}]")
        except Exception as error:
            logger.error(f" Error: {error}")
        return

    csv_path = args.input
    if not csv_path and os.path.exists("data/contributions_cleaned.csv"):
        csv_path = "data/contributions_cleaned.csv"
    if not csv_path or not os.path.exists(csv_path):
        logger.info("Error: no cleaned CSV found")
        sys.exit(1)

    data_dir = os.path.dirname(csv_path) or "."

    prev_cache = Cache(os.path.join(data_dir, PREV_EMPLOYER_CACHE))
    addr_cache = Cache(os.path.join(data_dir, EMPLOYER_ADDR_CACHE))
    comm_cache = Cache(os.path.join(data_dir, COMMITTEE_CACHE))
    branch_cache = Cache(os.path.join(data_dir, EMPLOYER_BRANCH_CACHE))

    # read_pipeline_csv preserves literal "NULL" surnames.
    from fec.io import read_pipeline_csv
    df = read_pipeline_csv(csv_path)
    df["contribution_receipt_amount"] = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").fillna(0)

    donor_totals = _compute_donor_totals(df)
    active_tiers = [tier[0] for tier in TIERS]

    if args.stats:
        show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)
        return

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Resolve - Employer & Committee Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"{'=' * 60}")

    total_start = time.time()

    # Step 0 injects curated addresses first so AI lookups don't waste calls on
    # them and dedup treats them as authoritative.
    logger.info("\n-- Step 0: Manual overrides --")
    load_manual_overrides(Path(data_dir) / "manual_employer_addresses.csv", addr_cache)
    load_manual_branches(Path(data_dir) / "manual_employer_addresses.csv", branch_cache)
    load_manual_committee_overrides(Path(data_dir) / "manual_committee_addresses.csv", comm_cache)

    logger.info("\n-- Step 1: Cross-Record (find previous employer from our data) --")
    step_cross_record(df, prev_cache)

    logger.info("\n-- Step 2: FEC API (find previous employer from other committees) --")
    step_fec_api(df, prev_cache, donor_totals, active_tiers, dry_run=args.dry_run)

    ai_provider, ai_model = get_ai_provider_model()
    logger.info(f"\n-- Step 3: AI Lookup ({ai_provider} {ai_model} - employer HQ addresses) --")
    step_ai_lookup(df, prev_cache, addr_cache, donor_totals, active_tiers, dry_run=args.dry_run)

    # Step 3b: merge cache entries that resolved to the same address, then
    # remap the CSV to the canonical names - otherwise apply_results
    # lookups fail for merged-away variants.
    if not args.dry_run:
        logger.info("\n-- Step 3b: Same-address dedup --")
        # CSV frequencies make the canonical the variant donors most often wrote.
        frequencies = df["contributor_employer"].value_counts().to_dict()
        removed, groups, mapping = dedup_by_resolved_address(addr_cache, freq=frequencies)
        logger.info(f"    merged {removed:,} cache entries across {groups:,} groups")
        if mapping:
            employer_col = df["contributor_employer"]
            to_remap = employer_col.isin(mapping)
            n_csv = int(to_remap.sum())
            if n_csv:
                df.loc[to_remap, "contributor_employer"] = employer_col[to_remap].map(mapping)
                logger.info(f"    rewrote {n_csv:,} CSV rows to canonical names")

    logger.info("\n-- Step 4: Committee Addresses (from FEC filings) --")
    step_committees_own_address(df, comm_cache)

    if args.apply and not args.dry_run:
        logger.info(f"\n-- Writing results -> {csv_path} --")
        df = apply_results(df, prev_cache, addr_cache, comm_cache, branch_cache)

        # Resolve must KEEP resolve_method - geocode --employer-only reads it for
        # self-employed coords; the last writer (geocode) drops provenance cols.

        # Expand Saint/Mount/Fort in the resolved employer city - same rule as
        # donor cities in clean.py.
        if "employer_city" in df.columns:
            from fec.config import expand_city_abbreviations
            df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

        df.to_csv(csv_path, index=False)
        logger.info(f"  Written {len(df):,} rows")
    elif not args.dry_run:
        # Without this hint users assume the run wrote employer_address, then
        # `geocode.py --employer-only` fails with "No employer_address column".
        logger.info(
            f"\n  Caches updated - the CSV was NOT modified.\n"
            f"    Re-run with --apply to write employer_address/city/state onto\n"
            f"    {csv_path} (needed before `geocode.py --employer-only`)."
        )

    elapsed = time.time() - total_start
    minutes, seconds = divmod(int(elapsed), 60)
    logger.info(f"\n  Time: {minutes}m {seconds}s")

    show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)
