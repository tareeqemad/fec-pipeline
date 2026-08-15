#!/usr/bin/env python3
"""Geocode donors or build geocoded employer locations."""

import argparse
import json
import os
import sys

import pandas as pd

from fec.cleaning.previous_employer import referenced_employers
from fec.env import CLEANED_CSV
from fec.geocoding import (
    GeoCache,
    apply_employer_to_dataframe,
    apply_to_dataframe,
    geocode_addresses,
    geocode_employer_addresses,
)
from fec.log import get_logger
from fec.resolve.pipeline.constants import EMPLOYER_ADDR_CACHE
from fec.resolve.pipeline.locations import (
    address_cache_lookup,
    location_candidates,
    resolve_cache_entry,
)

logger = get_logger(__name__)


def _find_csv() -> str:
    if CLEANED_CSV.exists():
        return str(CLEANED_CSV)
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


def _parse_args():
    parser = argparse.ArgumentParser(description="Geocode FEC contributions")
    parser.add_argument(
        "--employer-only",
        action="store_true",
        help="Geocode employers and build employer_locations.csv",
    )
    return parser.parse_args()


def _geocode_contributors(
    df: pd.DataFrame,
    cache: GeoCache,
) -> pd.DataFrame:
    logger.info("\n-- Contributor Addresses --")
    geocode_addresses(df, cache)
    df = apply_to_dataframe(df, cache)

    has_coordinates = df["latitude"].notna()
    logger.info(
        f"\n  Contributor coords: {has_coordinates.sum():,} / {len(df):,} "
        f"({has_coordinates.mean() * 100:.1f}%)"
    )

    from fec.donor_match.canonicalize import canonicalize_donor_addresses_geo

    unified = canonicalize_donor_addresses_geo(df)
    if unified:
        logger.info(
            f"  Geo-dedup: {unified:,} rows unified to a per-donor same-place address"
        )
    return df


def _geocode_employers(
    df: pd.DataFrame,
    cache: GeoCache,
    data_dir: str,
) -> tuple[pd.DataFrame, bool]:
    if "employer_address" not in df.columns:
        logger.info("\n  No employer_address column - run resolve.py --apply first")
        return df, False

    logger.info("\n-- Employer Addresses --")
    addresses = _all_employer_addresses(df, data_dir)
    geocode_employer_addresses(addresses, cache)
    df = apply_employer_to_dataframe(df, cache)

    has_coordinates = df["employer_latitude"].notna()
    logger.info(
        f"\n  Employer coords: {has_coordinates.sum():,} / {len(df):,} "
        f"({has_coordinates.mean() * 100:.1f}%)"
    )
    for source, count in df["employer_geocode_level"].value_counts().items():
        logger.info(f"    {source:25s} {count:>7,}")
    return df, True


def _write_output(df: pd.DataFrame, csv_path: str) -> None:
    from fec.config.data import INTERNAL_OUTPUT_COLUMNS

    df = df.drop(columns=INTERNAL_OUTPUT_COLUMNS, errors="ignore")
    logger.info(f"\n  Writing -> {csv_path}")
    df.to_csv(csv_path, index=False)


def main():
    args = _parse_args()

    csv_path = _find_csv()
    data_dir = os.path.dirname(csv_path) or "."
    cache_path = os.path.join(data_dir, "geocode_cache.json")
    cache = GeoCache(cache_path)

    logger.info("=" * 60)
    logger.info("  FEC Geocoder")
    logger.info("=" * 60)
    logger.info(f"\n  File: {csv_path}")

    from fec.io import read_pipeline_csv

    df = read_pipeline_csv(csv_path)
    logger.info(f"  Rows: {len(df):,}")

    if not args.employer_only:
        df = _geocode_contributors(df, cache)
        changed = True
    else:
        df, changed = _geocode_employers(df, cache, data_dir)

    if changed:
        _write_output(df, csv_path)
        if args.employer_only:
            from build_employers import build

            logger.info("\n-- Building employer locations --")
            build()

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
