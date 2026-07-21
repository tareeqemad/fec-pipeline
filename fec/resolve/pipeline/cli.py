"""CLI entry point for the resolve pipeline."""

import argparse
import os
import sys
import time

import pandas as pd

from fec.log import get_logger

from .constants import (
    TIERS, EMPLOYER_ADDR_CACHE, PREV_EMPLOYER_CACHE, COMMITTEE_CACHE,
)
from .cache import Cache
from .ai_client import get_ai_client, get_ai_provider_model, ai_json_call
from .helpers import _load_env, _compute_donor_totals
from .prompts import AI_SYSTEM_PROMPT
from .steps.cross_record import step_cross_record
from .steps.fec_api import step_fec_api
from .steps.ai_employer import step_ai_lookup, _parse_ai_json
from .steps.committee_address import step_committees_own_address
from .apply import apply_results
from .dedup import dedup_by_resolved_address
from .manual_overrides import load_manual_overrides, load_manual_committee_overrides
from .stats import show_stats

logger = get_logger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(description="Resolve employer/committee addresses")
    p.add_argument("input", nargs="?", default=None)
    p.add_argument("--tier", type=int, nargs="+", metavar="N",
                   help="Only process specific tiers (1=top, 6=bottom)")
    p.add_argument("--skip-ai", action="store_true", help="Skip AI lookup")
    p.add_argument("--skip-fec", action="store_true", help="Skip FEC API")
    p.add_argument("--skip-committees", action="store_true", help="Skip committee lookup")
    p.add_argument("--dry-run", action="store_true", help="Count without API calls")
    p.add_argument("--stats", action="store_true", help="Show status")
    p.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    p.add_argument("--test-ai", action="store_true", help="Test the AI provider API key and exit")
    args = p.parse_args()

    _load_env()

    # Test AI mode
    if args.test_ai:
        try:
            client, model, provider = get_ai_client()
        except ImportError:
            logger.info("\u2717 openai package not installed \u2014 run: pip install openai")
            sys.exit(1)
        if client is None:
            sys.exit(1)
        logger.info(f"  Provider: {provider}")
        logger.info(f"  Model: {model}")
        logger.info(f"  Testing: 'GOLDMAN SACHS, GOOGLE'...")
        try:
            text = ai_json_call(
                client, model, provider,
                AI_SYSTEM_PROMPT, '1. "GOLDMAN SACHS"\n2. "GOOGLE"',
                max_tokens=2000,
            )
            results = _parse_ai_json(text)
            logger.info(f"  \u2713 Response:")
            for r in results:
                if not isinstance(r, dict):
                    continue
                logger.info(f"    {r['name']}: {r.get('address','?')}, {r.get('city','?')}, {r.get('state','?')} [{r.get('confidence','?')}]")
        except Exception as e:
            logger.error(f" Error: {e}")
        return

    # Find CSV
    csv_path = args.input
    if not csv_path:
        for path in ["data/contributions_cleaned.csv"]:
            if os.path.exists(path):
                csv_path = path
                break
    if not csv_path or not os.path.exists(csv_path):
        logger.info("Error: no cleaned CSV found")
        sys.exit(1)

    data_dir = os.path.dirname(csv_path) or "."

    # Load caches
    prev_cache = Cache(os.path.join(data_dir, PREV_EMPLOYER_CACHE))
    addr_cache = Cache(os.path.join(data_dir, EMPLOYER_ADDR_CACHE))
    comm_cache = Cache(os.path.join(data_dir, COMMITTEE_CACHE))

    # Load data. read_pipeline_csv preserves literal "NULL" surnames.
    from fec.io import read_pipeline_csv
    df = read_pipeline_csv(csv_path)
    df["contribution_receipt_amount"] = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").fillna(0)

    donor_totals = _compute_donor_totals(df)
    active_tiers = args.tier if args.tier else [t[0] for t in TIERS]

    # Stats mode
    if args.stats:
        show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)
        return

    # Header
    logger.info(f"\n{'\u2550' * 60}")
    logger.info(f"  FEC Resolve \u2014 Employer & Committee Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"  Tiers: {', '.join(str(t) for t in active_tiers)}")
    logger.info(f"{'\u2550' * 60}")

    total_start = time.time()

    # Step 0: Manual overrides \u2014 inject curated HQ addresses before anything
    # else, so AI lookups don't waste calls on them and any merge/dedup
    # downstream sees them as authoritative.
    from pathlib import Path
    logger.info(f"\n\u2500\u2500 Step 0: Manual overrides \u2500\u2500")
    load_manual_overrides(Path(data_dir) / "manual_employer_addresses.csv", addr_cache)
    load_manual_committee_overrides(Path(data_dir) / "manual_committee_addresses.csv", comm_cache)

    # Step 1: Cross-record (free, all tiers at once)
    logger.info(f"\n\u2500\u2500 Step 1: Cross-Record (find previous employer from our data) \u2500\u2500")
    step_cross_record(df, prev_cache)

    # Step 2: FEC API for RETIRED (free)
    if not args.skip_fec:
        logger.info(f"\n\u2500\u2500 Step 2: FEC API (find previous employer from other committees) \u2500\u2500")
        step_fec_api(df, prev_cache, donor_totals, active_tiers, dry_run=args.dry_run)

    # Step 3: AI Lookup (employer HQ addresses)
    if not args.skip_ai:
        ai_provider, ai_model = get_ai_provider_model()
        logger.info(f"\n\u2500\u2500 Step 3: AI Lookup ({ai_provider} {ai_model} \u2014 employer HQ addresses) \u2500\u2500")
        step_ai_lookup(df, prev_cache, addr_cache, donor_totals, active_tiers, dry_run=args.dry_run)

        # Step 3b: Same-address dedup \u2014 merge cache entries that resolved
        # to the same address AND are plausibly the same company. Also
        # apply the resulting variant\u2192canonical mapping to the dataframe
        # so the CSV's contributor_employer matches the cache (otherwise
        # apply_results lookups fail for merged-away variants).
        if not args.dry_run:
            logger.info(f"\n\u2500\u2500 Step 3b: Same-address dedup \u2500\u2500")
            # Pass current CSV frequencies so the canonical is the variant
            # donors most often actually wrote (not just longest cache key).
            freq = (df["contributor_employer"].astype(str).str.strip().str.upper()
                    .value_counts().to_dict())
            removed, groups, mapping = dedup_by_resolved_address(addr_cache, freq=freq)
            logger.info(f"    merged {removed:,} cache entries across {groups:,} groups")
            if mapping:
                emp_u = df["contributor_employer"].astype(str).str.strip().str.upper()
                to_remap = emp_u.isin(mapping)
                n_csv = int(to_remap.sum())
                if n_csv:
                    df.loc[to_remap, "contributor_employer"] = emp_u[to_remap].map(mapping)
                    logger.info(f"    rewrote {n_csv:,} CSV rows to canonical names")

    # Step 4: Committee addresses \u2014 straight from the committees' own FEC
    # filings (free, no lookup; committees record their own address).
    if not args.skip_committees:
        logger.info(f"\n\u2500\u2500 Step 4: Committee Addresses (from FEC filings) \u2500\u2500")
        step_committees_own_address(df, comm_cache)

    # Apply to CSV
    if args.apply and not args.dry_run:
        logger.info(f"\n\u2500\u2500 Writing results \u2192 {csv_path} \u2500\u2500")
        df = apply_results(df, prev_cache, addr_cache, comm_cache)

        # NOTE: resolve must KEEP resolve_method here — the final pipeline step
        # (geocode --employer-only) reads it for self-employed employer coords.
        # Internal/provenance columns are dropped by that last writer (geocode).

        # Expand Saint/Mount/Fort abbreviations in the resolved employer city
        # (from the AI/manual cache, e.g. "St. Louis") so the cleaned file
        # carries no city abbreviations \u2014 same rule as donor cities in clean.py.
        if "employer_city" in df.columns:
            from fec.config import expand_city_abbreviations
            df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

        df.to_csv(csv_path, index=False)
        logger.info(f"  \u2713 Written {len(df):,} rows")
    elif not args.dry_run:
        # Caches were updated but the CSV was NOT touched. Without this hint
        # users assume the run wrote employer_address \u2014 then `geocode.py
        # --employer` fails with "No employer_address column".
        logger.info(
            f"\n  \u2139 Caches updated \u2014 the CSV was NOT modified.\n"
            f"    Re-run with --apply to write employer_address/city/state onto\n"
            f"    {csv_path} (needed before `geocode.py --employer`)."
        )

    # Summary
    elapsed = time.time() - total_start
    m, s = divmod(int(elapsed), 60)
    logger.info(f"\n  Time: {m}m {s}s")

    show_stats(df, prev_cache, addr_cache, comm_cache, donor_totals)
