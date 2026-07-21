#!/usr/bin/env python3
"""
geocode.py — Add lat/lng coordinates to addresses.

    python geocode.py              # geocode contributor addresses
    python geocode.py --employer   # also geocode employer addresses (after resolve.py)
    python geocode.py --stats      # show cache progress

Contributor geocoding adds: latitude, longitude, geocode_level
Employer geocoding adds:   employer_latitude, employer_longitude, employer_geocode_level

For SELF-EMPLOYED/RETIRED: employer coords = contributor coords (same address).
"""

import argparse
import os
import sys
import glob
from pathlib import Path

import pandas as pd

from fec.geocoding import (
    GeoCache,
    geocode_addresses, apply_to_dataframe,
    geocode_employer_addresses, apply_employer_to_dataframe,
)
from fec.env import load_env, get_env
from fec.log import get_logger

logger = get_logger(__name__)
load_env()


def _load_google_key() -> str | None:
    return get_env("GOOGLE_MAPS_API_KEY") or None


def _find_csv() -> str:
    for path in ["data/contributions_cleaned.csv"]:
        if os.path.exists(path):
            return path
    for path in sorted(glob.glob("data/*_cleaned.csv")):
        return path
    logger.info("  Error: no cleaned CSV found in data/")
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Geocode FEC contributions")
    parser.add_argument("input", nargs="?", default=None)
    parser.add_argument("--employer", action="store_true",
                        help="Also geocode employer addresses (needs resolve.py first)")
    parser.add_argument("--employer-only", action="store_true",
                        help="Only geocode employer addresses (skip contributor)")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--validate", action="store_true",
                        help="Remove cached entries with coords outside their state")
    args = parser.parse_args()

    csv_path   = args.input or _find_csv()
    data_dir   = os.path.dirname(csv_path) or "."
    cache_path = os.path.join(data_dir, "geocode_cache.json")
    cache      = GeoCache(cache_path)

    # ── Stats ──

    if args.stats:
        if not len(cache):
            logger.info("  Cache is empty — run geocode.py first.")
            return
        s = cache.stats()
        logger.info(f"\n  Cache:   {cache_path}")
        logger.info(f"  Total:   {s['total']:,}  |  Found: {s['found']:,}  |  Failed: {s['failed']:,}")
        for src, cnt in sorted(s["by_source"].items(), key=lambda x: -x[1]):
            logger.info(f"    {src:25s} {cnt:>7,}")
        return

    # ── Validate ──

    if args.validate:
        from fec.geocoding.pipeline import _valid_for_state
        removed = 0
        for key in list(cache.data.keys()):
            val = cache.data[key]
            if val.get('lat') is None:
                continue
            parts = key.split('|')
            if len(parts) >= 3:
                state = parts[2]
                if not _valid_for_state(val['lat'], val['lng'], state):
                    logger.info(f"  ✗ {key}: lat={val['lat']:.4f} outside {state}")
                    del cache.data[key]
                    removed += 1
        if removed:
            cache.save()
            logger.info(f"\n  Removed {removed:,} bad entries — run geocode.py to re-geocode them")
        else:
            logger.info("  ✓ All cached coords valid")
        return

    logger.info("=" * 60)
    logger.info("  FEC Geocoder")
    logger.info("=" * 60)
    logger.info(f"\n  File: {csv_path}")

    from fec.io import read_pipeline_csv
    df = read_pipeline_csv(csv_path)
    logger.info(f"  Rows: {len(df):,}")

    google_key = _load_google_key()
    changed = False

    # ── Pass 1: Contributor addresses ──

    if not args.employer_only:
        logger.info(f"\n── Contributor Addresses ──")
        geocode_addresses(df, cache, google_key=google_key, batch_size=args.batch_size)
        df = apply_to_dataframe(df, cache)
        changed = True

        has = df["latitude"].notna()
        logger.info(f"\n  Contributor coords: {has.sum():,} / {len(df):,} ({has.mean()*100:.1f}%)")

        # Now that coords exist, collapse a donor's same-place addresses written
        # differently (string pass can't see they're the same building).
        from fec.database.donor_match.output import canonicalize_donor_addresses_geo
        n_geo = canonicalize_donor_addresses_geo(df)
        if n_geo:
            logger.info(f"  Geo-dedup: {n_geo:,} rows unified to a per-donor same-place address")

    # ── Pass 2: Employer addresses ──

    if args.employer or args.employer_only:
        if "employer_address" not in df.columns:
            logger.info(f"\n  ⚠ No employer_address column — run resolve.py first")
        else:
            logger.info(f"\n── Employer Addresses ──")
            geocode_employer_addresses(df, cache, google_key=google_key, batch_size=args.batch_size)
            df = apply_employer_to_dataframe(df, cache)
            changed = True

            has = df["employer_latitude"].notna() & (df["employer_latitude"] != 0)
            logger.info(f"\n  Employer coords: {has.sum():,} / {len(df):,} ({has.mean()*100:.1f}%)")
            for src, cnt in df["employer_geocode_level"].value_counts().items():
                logger.info(f"    {src:25s} {cnt:>7,}")

    # ── Write ──

    if changed:
        # Coordinates stay; the *_level provenance ("how it was geocoded") and
        # other internal columns never reach the cleaned file. Geocoding
        # idempotency comes from the on-disk cache, not these columns.
        from fec.config import INTERNAL_OUTPUT_COLUMNS
        df = df.drop(columns=[c for c in INTERNAL_OUTPUT_COLUMNS if c in df.columns],
                     errors="ignore")
        logger.info(f"\n  Writing → {csv_path}")
        df.to_csv(csv_path, index=False)

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
