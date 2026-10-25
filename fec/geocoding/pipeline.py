"""Cache, OpenStreetMap, then Census for each cleaned address."""

import time

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign import kept_as_filed_mask
from fec.cleaning.addresses.fixes.verified import apply_verified_address_fixes, save_verified_city_corrections
from fec.geocoding.cache import GeoCache
from fec.geocoding.providers import CensusUnavailable, NominatimUnavailable, census, nominatim
from fec.geocoding.validation import accepted_coordinates, confirmed_city, is_po_box, reviewed_point, unclear_house_number
from fec.log import get_logger

logger = get_logger(__name__)


def _contributor_keys(df: pd.DataFrame) -> pd.Series:
    return df[["contributor_street_1", "contributor_city", "contributor_state",
               "contributor_zip"]].fillna("").astype(str).agg("|".join, axis=1)


def _employer_keys(df: pd.DataFrame) -> pd.Series:
    return df[["employer_address", "employer_city", "employer_state",
               "employer_zip"]].fillna("").astype(str).agg("|".join, axis=1)


# Retry missing or temporary failures; keep completed misses in the cache.
def _needs_lookup(key: str, cache: GeoCache) -> bool:
    if reviewed_point(key) is not None:
        return False
    entry = cache.get(key)
    if cache.needs_retry(key):
        return True
    if entry.get("lookup_status", entry.get("source")) == "not_found":
        return False
    return accepted_coordinates(key, entry)[0] is None


# Try OpenStreetMap, then Census; keep an unmatched address blank.
def _geocode_one(street: str, city: str, state: str, zipcode: str) -> dict | None:
    if not street.strip() or is_po_box(street) or unclear_house_number(street):
        return None
    key = "|".join((street, city, state, zipcode))
    reviewed = reviewed_point(key)
    if reviewed is not None:
        return {"lat": reviewed[0], "lng": reviewed[1], "country": "US", "source": "google"}
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


# Look up each uncached address once.
def _geocode_keys(keys: pd.Series, cache: GeoCache, batch_size: int,
                  progress_logger, *, employer: bool = False) -> None:
    keys = list(dict.fromkeys(key for key in keys if key != "|||"))
    todo = [key for key in keys if _needs_lookup(key, cache)]
    progress_logger.info("%s: %s | cached: %s | remaining: %s",
                         "Employer addresses" if employer else "Addresses",
                         len(keys), len(keys) - len(todo), len(todo))
    if todo:
        _geocode_todo(todo, cache, batch_size)


# Save lookup results in batches and when the run stops.
def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    found = missed = transient = 0
    start = time.monotonic()
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
                logger.info("[%s/%s] found: %s | not found: %s | retry later: %s",
                            done, len(todo), found, missed, transient)
    finally:
        cache.save()
    logger.info("Finished %s addresses in %.0fs | found: %s | not found: %s | retry later: %s",
                len(todo), time.monotonic() - start, found, missed, transient)


# Look up donor addresses eligible for geocoding.
def geocode_addresses(df: pd.DataFrame, cache: GeoCache, batch_size: int = 50):
    keys = _contributor_keys(df)[~kept_as_filed_mask(df)]
    _geocode_keys(keys, cache, batch_size, logger)


# Save city evidence for clean to apply before donor matching.
def save_city_corrections(df: pd.DataFrame, cache: GeoCache) -> int:
    if "sub_id" not in df.columns:
        return 0
    keys = _contributor_keys(df.loc[~kept_as_filed_mask(df)])
    cities = {key: confirmed_city(key, cache.get(key)) for key in keys.unique()}
    rows = []
    for index, key in keys.items():
        city = cities[key]
        sub_id = df.at[index, "sub_id"]
        if city is None or pd.isna(sub_id) or not str(sub_id).strip():
            continue
        street, _, state, zipcode = key.split("|")
        rows.append({
            "match_street": street, "match_state": state, "match_zip": zipcode,
            "match_sub_id": str(sub_id).strip(), "street": street, "city": city,
            "source": cache.get(key)["matched_address"]["source_url"],
        })
    return save_verified_city_corrections(rows) if rows else 0


# Keep coordinates under both city names before clean applies the saved rules.
def prepare_city_corrections(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame | None:
    save_city_corrections(df, cache)
    corrected = df.copy()
    pending = apply_verified_address_fixes(corrected, city_only=True)
    if not pending:
        return None
    pairs = pd.DataFrame({'old': _contributor_keys(df), 'new': _contributor_keys(corrected)})
    saved = False
    for old, new in pairs.loc[pairs.old.ne(pairs.new)].drop_duplicates().itertuples(index=False, name=None):
        entry = cache.get(old)
        lat, lng, source = accepted_coordinates(old, entry)
        if lat is not None and accepted_coordinates(new, cache.get(new))[0] is None:
            cache.data[new] = {**(entry or {}), 'lat': lat, 'lng': lng, 'source': source}
            cache.data[new].pop('lookup_status', None)
            saved = True
    if saved:
        cache.save()
    return corrected


# Look up nonblank employer addresses.
def geocode_employer_addresses(df: pd.DataFrame, cache: GeoCache, batch_size: int = 50):
    keys = _employer_keys(df.loc[df["employer_address"].fillna("") != ""])
    _geocode_keys(keys, cache, batch_size, logger, employer=True)


# Read accepted coordinates once per distinct address.
def _cached_results(keys: pd.Series, cache: GeoCache) -> pd.Series:
    results = {}
    for key in keys.unique():
        lat, lng, level = accepted_coordinates(key, cache.get(key))
        results[key] = (np.nan, np.nan, level) if lat is None else (lat, lng, level)
    return keys.map(results)


# Apply donor coordinates, leaving preserved foreign filings ungeocoded.
def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    results = _cached_results(_contributor_keys(df), cache)
    df["latitude"] = results.map(lambda point: point[0])
    df["longitude"] = results.map(lambda point: point[1])
    df["geocode_level"] = results.map(lambda point: point[2])
    held = kept_as_filed_mask(df)
    df.loc[held, ["latitude", "longitude"]] = np.nan
    df.loc[held, "geocode_level"] = "foreign_not_geocoded"
    return df


# Apply employer coordinates only to resolved addresses.
def apply_employer_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    df["employer_latitude"] = np.nan
    df["employer_longitude"] = np.nan
    df["employer_geocode_level"] = "no_address"
    method = df.get("resolve_method", pd.Series("", index=df.index)).fillna("")
    mask = (method != "fec_not_found") & (df["employer_address"].fillna("") != "")
    results = _cached_results(_employer_keys(df.loc[mask]), cache)
    for index, column in enumerate(("employer_latitude", "employer_longitude", "employer_geocode_level")):
        df.loc[mask, column] = results.map(lambda point: point[index]).values
    return df
