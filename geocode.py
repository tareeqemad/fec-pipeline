#!/usr/bin/env python3
"""Geocode donors or build geocoded employer locations."""

import argparse
import json
import os
import sys

import pandas as pd

from fec.cleaning.employer_status import referenced_employers
from fec.contract import RESOLVE_WORKING, STAGES, check_input, check_output
from fec.env import CACHE_DIR, CLEANED_CSV, EMPLOYER_LOCATIONS_CSV, GEOCODE_CACHE_JSON
from fec.geocoding.cache import GeoCache
from fec.geocoding.employers import (
    apply_employer_to_dataframe,
    geocode_employer_addresses,
)
from fec.geocoding.pipeline import apply_to_dataframe, geocode_addresses
from fec.io import write_csv_atomic
from fec.log import get_logger
from fec.pipeline_run import check_same_run, record
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


def _all_employer_addresses(df: pd.DataFrame) -> pd.DataFrame:
    """Include every published employer location."""
    path = os.path.join(CACHE_DIR, EMPLOYER_ADDR_CACHE)
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
    return df


def _geocode_employers(
    df: pd.DataFrame,
    cache: GeoCache,
) -> tuple[pd.DataFrame, bool]:
    if "employer_address" not in df.columns:
        logger.info("\n  No employer_address column - run resolve.py --apply first")
        return df, False

    logger.info("\n-- Employer Addresses --")
    addresses = _all_employer_addresses(df)
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


def _write_output(df: pd.DataFrame, csv_path: str, stage: str, input_columns) -> None:
    from fec.config.data import INTERNAL_OUTPUT_COLUMNS

    # resolve's working columns stay until the employer stage has read them
    kept = set(RESOLVE_WORKING) - set(STAGES[stage].drops)
    df = df.drop(columns=[c for c in INTERNAL_OUTPUT_COLUMNS if c not in kept], errors="ignore")
    check_output(stage, input_columns, df.columns)
    logger.info(f"\n  Writing -> {csv_path}")
    write_csv_atomic(df, csv_path, index=False)
    record(stage, csv_path)


def main():
    args = _parse_args()

    csv_path = _find_csv()
    cache = GeoCache(str(GEOCODE_CACHE_JSON))

    logger.info("=" * 60)
    logger.info("  FEC Geocoder")
    logger.info("=" * 60)
    logger.info(f"\n  File: {csv_path}")

    from fec.io import read_pipeline_csv

    stage = "employer_geocode" if args.employer_only else "geocode"
    check_same_run(csv_path)
    df = read_pipeline_csv(csv_path)
    check_input(stage, df.columns)
    input_columns = list(df.columns)
    logger.info(f"  Rows: {len(df):,}")

    if not args.employer_only:
        df = _geocode_contributors(df, cache)
        changed = True
    else:
        df, changed = _geocode_employers(df, cache)

    if changed:
        _write_output(df, csv_path, stage, input_columns)
        if args.employer_only:
            from fec.geocoding.employer_locations import build

            logger.info("\n-- Building employer locations --")
            build()
            record("employers", csv_path, EMPLOYER_LOCATIONS_CSV)

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
