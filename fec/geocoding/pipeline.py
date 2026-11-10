"""Find map points for the cleaned addresses: cache, OpenStreetMap, then Census.

Run: python geocode.py

Steps, in order (main):
    1. read_pipeline_csv       data/output/contributions_cleaned.csv
    2. geocode_addresses       look up each address the cache does not hold (providers.py)
    3. apply_coordinates       latitude and longitude from the cache; foreign filings stay empty
    4. _write_output           save the CSV with the coordinates
"""

import os
import sys
import time

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign_addresses import kept_as_filed_mask
from fec.common.columns import check_input, check_output
from fec.common.files import write_csv_atomic
from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV, GEOCODE_CACHE_JSON, PROJECT_ROOT, load_env
from fec.geocoding.cache import GeoCache
from fec.geocoding.coordinate_checks import accepted_coordinates, is_po_box, unclear_house_number
from fec.geocoding.providers import CensusUnavailable, NominatimUnavailable, census, nominatim

logger = get_logger(__name__)


def _build_cache_keys(df: pd.DataFrame) -> pd.Series:
    """Return each row's cache key: street|city|state|zip."""
    return (
        df[["contributor_street_1", "contributor_city", "contributor_state", "contributor_zip"]]
        .fillna("")
        .astype(str)
        .agg("|".join, axis=1)
    )


def _needs_lookup(key: str, cache: GeoCache) -> bool:
    """Retry missing or temporary failures; keep completed misses."""
    entry = cache.get(key)
    if cache.needs_retry(key):
        return True
    if entry.get("lookup_status", entry.get("source")) == "not_found":
        return False
    return accepted_coordinates(key, entry)[0] is None


def _geocode_one(street: str, city: str, state: str, zipcode: str) -> dict | None:
    """Try OpenStreetMap, then Census; keep an unmatched address blank."""
    if not street.strip() or is_po_box(street) or unclear_house_number(street):
        return None
    key = "|".join((street, city, state, zipcode))
    unavailable = None
    for source, provider in (("nominatim", nominatim), ("census", census)):
        try:
            entry = provider(street, city, state, zipcode)
        except (NominatimUnavailable, CensusUnavailable) as error:
            unavailable = error
            continue
        if entry is None:
            continue
        entry = {**entry, "source": source}
        if accepted_coordinates(key, entry)[0] is not None:
            return entry
    if unavailable is not None:
        raise unavailable
    return None


def _geocode_keys(keys: pd.Series, cache: GeoCache, batch_size: int) -> None:
    """Look up each uncached address once."""
    keys = list(dict.fromkeys(key for key in keys if key != "|||"))
    todo = [key for key in keys if _needs_lookup(key, cache)]
    logger.info(
        "Addresses: %s | cached: %s | remaining: %s",
        f"{len(keys):,}",
        f"{len(keys) - len(todo):,}",
        f"{len(todo):,}",
    )
    if todo:
        _geocode_todo(todo, cache, batch_size)


def _look_up_and_cache(key: str, cache: GeoCache) -> str:
    """Look up one address, cache the outcome, and return found, missed or transient."""
    try:
        entry = _geocode_one(*key.split("|"))
    except (NominatimUnavailable, CensusUnavailable) as error:
        cache.put_transient(key)
        logger.debug("Geocoding unavailable for %s: %s", key, error)
        return "transient"
    if entry is None:
        cache.put_failed(key)
        return "missed"
    cache.put(key, **entry)
    return "found"


def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    """Save lookup results in batches and when the run stops."""
    counts = {"found": 0, "missed": 0, "transient": 0}
    try:
        for done, key in enumerate(todo, 1):
            outcome = _look_up_and_cache(key, cache)
            counts[outcome] += 1
            if done % batch_size == 0:
                cache.save()
                logger.info(
                    "[%s/%s] found: %s | not found: %s | retry later: %s",
                    done,
                    len(todo),
                    counts["found"],
                    counts["missed"],
                    counts["transient"],
                )
    finally:
        cache.save()
    logger.info(
        "Lookups finished: %s | found: %s | not found: %s | retry later: %s",
        len(todo),
        counts["found"],
        counts["missed"],
        counts["transient"],
    )


def geocode_addresses(df: pd.DataFrame, cache: GeoCache, batch_size: int = 50) -> None:
    """Look up donor addresses eligible for geocoding."""
    keys = _build_cache_keys(df)[~kept_as_filed_mask(df)]
    _geocode_keys(keys, cache, batch_size)


def apply_coordinates(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Apply donor coordinates, leaving preserved foreign filings ungeocoded."""
    keys = _build_cache_keys(df)
    latitudes, longitudes = {}, {}
    for key in keys.unique():
        latitude, longitude, _ = accepted_coordinates(key, cache.get(key))
        latitudes[key] = latitude
        longitudes[key] = longitude
    df["latitude"] = keys.map(latitudes).astype(float)
    df["longitude"] = keys.map(longitudes).astype(float)
    df.loc[kept_as_filed_mask(df), ["latitude", "longitude"]] = np.nan
    return df


def _find_csv() -> str:
    """Find the cleaned file or stop with a clear message."""
    if CLEANED_CSV.exists():
        return str(CLEANED_CSV)
    logger.error("No cleaned CSV found. Run python clean.py first.")
    sys.exit(1)


def _geocode_contributors(
    df: pd.DataFrame,
    cache: GeoCache,
) -> pd.DataFrame:
    """Look up donor addresses and apply cached coordinates."""
    logger.info("[1/2] Finding contributor coordinates")
    geocode_addresses(df, cache)
    df = apply_coordinates(df, cache)

    logger.info(
        "Rows with contributor coordinates: %s / %s",
        f"{df['latitude'].notna().sum():,}",
        f"{len(df):,}",
    )
    return df


def _write_output(df: pd.DataFrame, csv_path: str, input_columns: list[str]) -> None:
    """Check the output columns and save the CSV."""
    from fec.common.columns import INTERNAL_OUTPUT_COLUMNS

    df = df.drop(columns=INTERNAL_OUTPUT_COLUMNS, errors="ignore")
    check_output("geocode", input_columns, df.columns)
    write_csv_atomic(df, csv_path, index=False)


def main() -> None:
    """Read the cleaned data, geocode the selected addresses and save."""
    start = time.perf_counter()
    load_env()

    csv_path = _find_csv()
    cache = GeoCache(str(GEOCODE_CACHE_JSON))

    from fec.common.files import read_pipeline_csv

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
