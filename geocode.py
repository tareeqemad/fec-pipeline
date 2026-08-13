#!/usr/bin/env python3
"""Add lat/lng to contributor addresses; --employer-only geocodes employer addresses (after resolve.py)."""

import argparse
import json
import os
import sys

import pandas as pd

from fec.geocoding import (
    GeoCache,
    geocode_addresses, apply_to_dataframe,
    geocode_employer_addresses, apply_employer_to_dataframe,
)
from fec.log import get_logger
from fec.cleaning.previous_employer import referenced_employers
from fec.resolve.pipeline.constants import EMPLOYER_ADDR_CACHE
from fec.resolve.pipeline.locations import (
    address_cache_lookup,
    location_candidates,
    resolve_cache_entry,
)

logger = get_logger(__name__)


def _find_csv() -> str:
    if os.path.exists("data/contributions_cleaned.csv"):
        return "data/contributions_cleaned.csv"
    logger.info("  Error: no cleaned CSV found in data/")
    sys.exit(1)


def _all_employer_addresses(df: pd.DataFrame, data_dir: str) -> pd.DataFrame:
    """Include every published employer location."""
    path = os.path.join(data_dir, EMPLOYER_ADDR_CACHE)
    if not os.path.exists(path):
        return df

    with open(path, encoding="utf-8") as handle:
        entries = json.load(handle)

    lookup = address_cache_lookup(entries)
    rows = []
    for employer in referenced_employers(df):
        entry = resolve_cache_entry(lookup, employer)
        rows.extend(location_candidates(entry))

    if not rows:
        return df
    return pd.concat([df, pd.DataFrame(rows)], ignore_index=True, sort=False)


def main():
    parser = argparse.ArgumentParser(description="Geocode FEC contributions")
    parser.add_argument("input", nargs="?", default=None)
    parser.add_argument("--employer-only", action="store_true",
                        help="Only geocode employer addresses (needs resolve.py --apply first)")
    parser.add_argument(
        "--cache-only", action="store_true",
        help="Apply saved coordinates without external requests",
    )
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args()

    csv_path   = args.input or _find_csv()
    data_dir   = os.path.dirname(csv_path) or "."
    cache_path = os.path.join(data_dir, "geocode_cache.json")
    cache      = GeoCache(cache_path)

    if args.stats:
        if not len(cache):
            logger.info("  Cache is empty - run geocode.py first.")
            return
        s = cache.stats()
        logger.info(f"\n  Cache:   {cache_path}")
        logger.info(f"  Total:   {s['total']:,}  |  Found: {s['found']:,}  |  Failed: {s['failed']:,}")
        for src, cnt in sorted(s["by_source"].items(), key=lambda x: -x[1]):
            logger.info(f"    {src:25s} {cnt:>7,}")
        return

    logger.info("=" * 60)
    logger.info("  FEC Geocoder")
    logger.info("=" * 60)
    logger.info(f"\n  File: {csv_path}")

    from fec.io import read_pipeline_csv
    df = read_pipeline_csv(csv_path)
    logger.info(f"  Rows: {len(df):,}")

    changed = False

    if not args.employer_only:
        logger.info("\n-- Contributor Addresses --")
        if not args.cache_only:
            geocode_addresses(df, cache)
        df = apply_to_dataframe(df, cache)
        changed = True

        has = df["latitude"].notna()
        logger.info(f"\n  Contributor coords: {has.sum():,} / {len(df):,} ({has.mean()*100:.1f}%)")

        # with coords, collapse a donor's same-place addresses written differently
        # (the string pass can't see they're the same building)
        from fec.donor_match.canonicalize import canonicalize_donor_addresses_geo
        n_geo = canonicalize_donor_addresses_geo(df)
        if n_geo:
            logger.info(f"  Geo-dedup: {n_geo:,} rows unified to a per-donor same-place address")

    if args.employer_only:
        if "employer_address" not in df.columns:
            logger.info("\n  No employer_address column - run resolve.py --apply first")
        else:
            logger.info("\n-- Employer Addresses --")
            addresses = _all_employer_addresses(df, data_dir)
            if not args.cache_only:
                geocode_employer_addresses(addresses, cache)
            df = apply_employer_to_dataframe(df, cache)
            changed = True

            has = df["employer_latitude"].notna()
            logger.info(f"\n  Employer coords: {has.sum():,} / {len(df):,} ({has.mean()*100:.1f}%)")
            for src, cnt in df["employer_geocode_level"].value_counts().items():
                logger.info(f"    {src:25s} {cnt:>7,}")

    if changed:
        # coords stay; *_level provenance and internal cols never reach the cleaned
        # file - geocoding idempotency comes from the on-disk cache, not these columns
        from fec.config import INTERNAL_OUTPUT_COLUMNS
        df = df.drop(columns=INTERNAL_OUTPUT_COLUMNS, errors="ignore")
        logger.info(f"\n  Writing -> {csv_path}")
        df.to_csv(csv_path, index=False)

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
