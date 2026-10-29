#!/usr/bin/env python3
"""Geocode donor addresses."""

import os
import sys
import time

import pandas as pd

from fec.contract import check_input, check_output
from fec.env import CLEANED_CSV, GEOCODE_CACHE_JSON, PROJECT_ROOT, load_env
from fec.geocoding.cache import GeoCache
from fec.geocoding.pipeline import (
    apply_to_dataframe,
    geocode_addresses,
    prepare_city_corrections,
)
from fec.io import write_csv_atomic
from fec.log import get_logger

logger = get_logger(__name__)


# Find the cleaned file or stop with a clear message.
def _find_csv() -> str:
    if CLEANED_CSV.exists():
        return str(CLEANED_CSV)
    logger.error("No cleaned CSV found. Run python clean.py first.")
    sys.exit(1)


# Look up donor addresses and apply cached coordinates.
def _geocode_contributors(
    df: pd.DataFrame,
    cache: GeoCache,
) -> pd.DataFrame:
    logger.info("[1/2] Finding contributor coordinates")
    geocode_addresses(df, cache)
    corrected = prepare_city_corrections(df, cache)
    if corrected is not None:
        if "employer_status" in df.columns:
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


# Check the output columns and save the CSV.
def _write_output(df: pd.DataFrame, csv_path: str, input_columns) -> None:
    from fec.config.data import INTERNAL_OUTPUT_COLUMNS

    df = df.drop(columns=INTERNAL_OUTPUT_COLUMNS, errors="ignore")
    check_output("geocode", input_columns, df.columns)
    write_csv_atomic(df, csv_path, index=False)


# Read the cleaned data, geocode the selected addresses and save.
def main():
    start = time.perf_counter()
    load_env()

    csv_path = _find_csv()
    cache = GeoCache(str(GEOCODE_CACHE_JSON))

    from fec.io import read_pipeline_csv

    df = read_pipeline_csv(csv_path)
    check_input("geocode", df.columns)
    input_columns = list(df.columns)
    logger.info("Reading: %s (%s rows)", os.path.relpath(csv_path, PROJECT_ROOT), f"{len(df):,}")

    df = _geocode_contributors(df, cache)

    logger.info("[2/2] Checking data and saving")
    _write_output(df, csv_path, input_columns)

    minutes, seconds = divmod(round(time.perf_counter() - start), 60)
    logger.info("Saved: %s", os.path.relpath(csv_path, PROJECT_ROOT))
    logger.info("Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python resolve.py --apply")


if __name__ == "__main__":
    main()
