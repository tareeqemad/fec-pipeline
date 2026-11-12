"""Find map points for the cleaned addresses: cache, OpenStreetMap, then Census.

Run: python geocode.py

Steps, in order (main):
    1. read_pipeline_csv       data/output/contributions_cleaned.csv
    2. read_geocode_cache      data/cache/geocode_cache.json: the answer of every earlier lookup, by address
    3. geocode_addresses       look up each address the cache does not hold (providers.py) and save the cache
    4. apply_coordinates       latitude and longitude from the cache; foreign filings stay empty
    5. write_csv_atomic        save the CSV with the coordinates
"""

import json
import sys
import time

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign_addresses import foreign_address_mask
from fec.common.columns import DONOR_COORDINATES, check_input, check_output
from fec.common.files import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV, GEOCODE_CACHE_JSON, PROJECT_ROOT, load_env
from fec.geocoding.coordinate_checks import accepted_coordinates, is_street_address
from fec.geocoding.providers import GeocoderUnavailable, census, nominatim

logger = get_logger(__name__)

SAVE_CACHE_EVERY = 50


def read_geocode_cache() -> dict:
    """Read the cache: {"street|city|state|zip": {"lat", "lng", "source", "country"}}, or empty on a first run."""
    if not GEOCODE_CACHE_JSON.exists():
        return {}
    return json.loads(GEOCODE_CACHE_JSON.read_text(encoding="utf-8"))


def _address_keys(df: pd.DataFrame) -> pd.Series:
    address = df[["contributor_street_1", "contributor_city", "contributor_state", "contributor_zip"]]
    return address.fillna("").astype(str).agg("|".join, axis=1)


def _needs_lookup(key: str, entry: dict | None) -> bool:
    if entry is None:
        return True
    last_lookup = entry.get("lookup_status", entry.get("source"))
    if last_lookup == "transient_fail":
        return True
    if last_lookup == "not_found":
        return False
    return accepted_coordinates(key, entry) is None


def _look_up(key: str) -> dict | None:
    street, city, state, zipcode = key.split("|")
    if not is_street_address(street):
        return None
    unavailable = None
    for provider in (nominatim, census):
        try:
            entry = provider(street, city, state, zipcode)
        except GeocoderUnavailable as error:
            unavailable = error
            continue
        if entry is not None and accepted_coordinates(key, entry) is not None:
            return entry
    if unavailable is not None:
        raise unavailable
    return None


def _look_up_and_record(key: str, cache: dict) -> str:
    try:
        entry = _look_up(key)
    except GeocoderUnavailable as error:
        logger.debug("Geocoding unavailable for %s: %s", key, error)
        entry, outcome = None, "transient_fail"
    else:
        outcome = "found" if entry else "not_found"
    earlier = cache.get(key)
    if entry:
        cache[key] = entry
    elif earlier and earlier.get("lat") is not None:
        # Keep coordinates an earlier run stored; only note how this lookup ended.
        cache[key] = {**earlier, "lookup_status": outcome}
    else:
        cache[key] = {"lat": None, "lng": None, "source": outcome}
    return outcome


def geocode_addresses(df: pd.DataFrame, cache: dict) -> None:
    """Look up the addresses the cache has no settled answer for, saving the cache as it grows."""
    keys = _address_keys(df)[~foreign_address_mask(df)]
    keys = [key for key in keys.unique() if key != "|||"]
    todo = [key for key in keys if _needs_lookup(key, cache.get(key))]
    logger.info(
        "Addresses: %s | cached: %s | remaining: %s", f"{len(keys):,}", f"{len(keys) - len(todo):,}", f"{len(todo):,}"
    )
    if not todo:
        return
    counts = {"found": 0, "not_found": 0, "transient_fail": 0}
    outcomes = "found: %s | not found: %s | retry later: %s"
    try:
        for done, key in enumerate(todo, 1):
            counts[_look_up_and_record(key, cache)] += 1
            if done % SAVE_CACHE_EVERY == 0:
                write_json_atomic(GEOCODE_CACHE_JSON, cache, ensure_ascii=False)
                logger.info("[%s/%s] " + outcomes, done, len(todo), *counts.values())
    finally:
        write_json_atomic(GEOCODE_CACHE_JSON, cache, ensure_ascii=False)
    logger.info("Lookups finished: %s | " + outcomes, len(todo), *counts.values())


def apply_coordinates(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    """Set latitude and longitude from the accepted cache entries; foreign filings stay empty."""
    keys = _address_keys(df)
    latitudes, longitudes = {}, {}
    for key in keys.unique():
        point = accepted_coordinates(key, cache.get(key))
        if point is not None:
            latitudes[key], longitudes[key] = point
    df["latitude"] = keys.map(latitudes).astype(float)
    df["longitude"] = keys.map(longitudes).astype(float)
    df.loc[foreign_address_mask(df), ["latitude", "longitude"]] = np.nan
    return df


def main() -> None:
    """Read the cleaned data, geocode the addresses not yet in the cache and save."""
    start = time.perf_counter()
    load_env()
    if not CLEANED_CSV.exists():
        logger.error("No cleaned CSV found. Run python clean.py first.")
        sys.exit(1)
    csv_name = CLEANED_CSV.relative_to(PROJECT_ROOT)
    df = read_pipeline_csv(CLEANED_CSV)
    check_input("geocode.py", df.columns)
    input_columns = list(df.columns)
    logger.info("Reading: %s (%s rows)", csv_name, f"{len(df):,}")

    logger.info("[1/2] Finding contributor coordinates")
    cache = read_geocode_cache()
    geocode_addresses(df, cache)
    df = apply_coordinates(df, cache)
    logger.info("Rows with contributor coordinates: %s / %s", f"{df['latitude'].notna().sum():,}", f"{len(df):,}")

    logger.info("[2/2] Checking data and saving")
    check_output("geocode.py", df.columns, [*input_columns, *DONOR_COORDINATES])
    write_csv_atomic(df, CLEANED_CSV, index=False)

    minutes, seconds = divmod(round(time.perf_counter() - start), 60)
    logger.info("Saved: %s", csv_name)
    logger.info("Completed in %dm %02ds.", minutes, seconds)
    logger.info("Next: python resolve.py --apply")
