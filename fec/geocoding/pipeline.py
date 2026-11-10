"""Cache, OpenStreetMap, then Census for each cleaned address."""

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign import kept_as_filed_mask
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import CensusUnavailable, NominatimUnavailable, census, nominatim
from fec.geocoding.validation import accepted_coordinates, is_po_box, unclear_house_number
from fec.log import get_logger

logger = get_logger(__name__)


def _contributor_keys(df: pd.DataFrame) -> pd.Series:
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
        "Addresses: %s | cached: %s | remaining: %s", f"{len(keys):,}", f"{len(keys) - len(todo):,}", f"{len(todo):,}"
    )
    if todo:
        _geocode_todo(todo, cache, batch_size)


def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    """Save lookup results in batches and when the run stops."""
    found = missed = transient = 0
    try:
        for done, key in enumerate(todo, 1):
            try:
                entry = _geocode_one(*key.split("|"))
            except (NominatimUnavailable, CensusUnavailable) as error:
                cache.put_transient(key)
                transient += 1
                logger.debug("Geocoding unavailable for %s: %s", key, error)
            else:
                if entry is None:
                    cache.put_failed(key)
                    missed += 1
                else:
                    cache.put(key, **entry)
                    found += 1
            if done % batch_size == 0:
                cache.save()
                logger.info(
                    "[%s/%s] found: %s | not found: %s | retry later: %s", done, len(todo), found, missed, transient
                )
    finally:
        cache.save()
    logger.info(
        "Lookups finished: %s | found: %s | not found: %s | retry later: %s", len(todo), found, missed, transient
    )


def geocode_addresses(df: pd.DataFrame, cache: GeoCache, batch_size: int = 50) -> None:
    """Look up donor addresses eligible for geocoding."""
    keys = _contributor_keys(df)[~kept_as_filed_mask(df)]
    _geocode_keys(keys, cache, batch_size)


def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Apply donor coordinates, leaving preserved foreign filings ungeocoded."""
    keys = _contributor_keys(df)
    points = {key: accepted_coordinates(key, cache.get(key))[:2] for key in keys.unique()}
    df["latitude"] = keys.map(lambda key: points[key][0]).astype(float)
    df["longitude"] = keys.map(lambda key: points[key][1]).astype(float)
    df.loc[kept_as_filed_mask(df), ["latitude", "longitude"]] = np.nan
    return df
