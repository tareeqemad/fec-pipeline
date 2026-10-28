#!/usr/bin/env python3
"""Geocode donors or build geocoded employer locations."""

import argparse
import json
import os
import sys
import time

import pandas as pd

from fec.cleaning.work.status import referenced_employers
from fec.contract import EMPLOYER_ADDRESS, RESOLVE_WORKING, STAGES, check_input, check_output
from fec.env import CACHE_DIR, CLEANED_CSV, GEOCODE_CACHE_JSON, PROJECT_ROOT, load_env
from fec.geocoding.cache import GeoCache
from fec.geocoding.pipeline import (
    apply_employer_to_dataframe,
    apply_to_dataframe,
    geocode_addresses,
    geocode_employer_addresses,
    prepare_city_corrections,
)
from fec.io import write_csv_atomic
from fec.log import get_logger
from fec.resolve.cache import EMPLOYER_ADDR_CACHE
from fec.resolve.locations import address_cache_lookup, location_candidates, resolve_cache_entry

logger = get_logger(__name__)


# Find the cleaned file or stop with a clear message.
def _find_csv() -> str:
    if CLEANED_CSV.exists():
        return str(CLEANED_CSV)
    logger.error("No cleaned CSV found. Run python clean.py first.")
    sys.exit(1)


# Include cached offices for every referenced employer.
def _all_employer_addresses(df: pd.DataFrame) -> pd.DataFrame:
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


# Read the employer-only option.
def _parse_args():
    parser = argparse.ArgumentParser(description="Geocode FEC contributions")
    parser.add_argument(
        "--employer-only",
        action="store_true",
        help="Geocode employers and build employer_locations.csv",
    )
    return parser.parse_args()


# Look up donor addresses and apply cached coordinates.
def _geocode_contributors(
    df: pd.DataFrame,
    cache: GeoCache,
) -> pd.DataFrame:
    logger.info("[1/2] Finding contributor coordinates")
    geocode_addresses(df, cache)
    corrected = prepare_city_corrections(df, cache)
    if corrected is not None:
        if set(df.columns) & {*EMPLOYER_ADDRESS, *RESOLVE_WORKING, "employer_status"}:
            raise ValueError("City corrections need clean.py, then geocode.py before resolve.py --apply")
        from clean import main as clean

        logger.info("Applying verified city corrections: running clean once before donor matching.")
        df = clean(expected=corrected)
    df = apply_to_dataframe(df, cache)

    logger.info(
        "Rows with contributor coordinates: %s / %s",
        f"{df['latitude'].notna().sum():,}", f"{len(df):,}",
    )
    return df


# Look up employer offices after resolve and apply their coordinates.
def _geocode_employers(
    df: pd.DataFrame,
    cache: GeoCache,
) -> pd.DataFrame:
    logger.info("[1/3] Finding employer coordinates")
    addresses = _all_employer_addresses(df)
    geocode_employer_addresses(addresses, cache)
    df = apply_employer_to_dataframe(df, cache)

    logger.info(
        "Rows with employer coordinates: %s / %s",
        f"{df['employer_latitude'].notna().sum():,}", f"{len(df):,}",
    )
    return df


# Check the output columns and save the CSV.
def _write_output(df: pd.DataFrame, csv_path: str, stage: str, input_columns) -> None:
    from fec.config.data import INTERNAL_OUTPUT_COLUMNS

    # resolve's working columns stay until the employer stage reads them.
    kept = set(RESOLVE_WORKING) - set(STAGES[stage].drops)
    df = df.drop(columns=[c for c in INTERNAL_OUTPUT_COLUMNS if c not in kept], errors="ignore")
    check_output(stage, input_columns, df.columns)
    write_csv_atomic(df, csv_path, index=False)


# Read the cleaned data, geocode the selected addresses and save.
def main():
    start = time.perf_counter()
    args = _parse_args()
    load_env()

    csv_path = _find_csv()
    cache = GeoCache(str(GEOCODE_CACHE_JSON))

    from fec.io import read_pipeline_csv

    stage = "employer_geocode" if args.employer_only else "geocode"
    df = read_pipeline_csv(csv_path)
    check_input(stage, df.columns)
    input_columns = list(df.columns)
    logger.info("Reading: %s (%s rows)", os.path.relpath(csv_path, PROJECT_ROOT), f"{len(df):,}")

    if not args.employer_only:
        df = _geocode_contributors(df, cache)
    else:
        df = _geocode_employers(df, cache)

    logger.info("[2/%s] Checking data and saving", 3 if args.employer_only else 2)
    _write_output(df, csv_path, stage, input_columns)
    if args.employer_only:
        from fec.resolve.employer_locations import build

        logger.info("[3/3] Saving employer locations")
        build()

    minutes, seconds = divmod(round(time.perf_counter() - start), 60)
    logger.info("Saved: %s", os.path.relpath(csv_path, PROJECT_ROOT))
    logger.info("Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python %s", "loader.py --reset" if args.employer_only else "resolve.py --apply")


if __name__ == "__main__":
    main()
