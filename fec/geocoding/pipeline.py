"""Address geocoding: the single-address engine chain plus the contributor and employer dataframe passes."""

import time

import numpy as np
import pandas as pd

from fec.cleaning.foreign_addresses import foreign_address_mask
from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS
from fec.geocoding.accepted import (
    STREET_LEVEL_SOURCES,
    _reviewed_wrong_street,
    _unchecked_outside_zip,
    accepted_coordinates,
    prefer_zip_centroids,
)
from fec.geocoding.address_kind import is_foreign_key, is_po_box
from fec.geocoding.cache import GeoCache
from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    CensusUnavailable,
    NominatimUnavailable,
)
from fec.geocoding.lookup import _geocode_one
from fec.geocoding.reviewed_points import is_reviewed_zip_typo, reviewed_point
from fec.geocoding.street_text import _numbered_streets
from fec.geocoding.zip_checks import (
    _far_from_zip,
    _zip_point,
)
from fec.log import get_logger

logger = get_logger(__name__)


# the per-row geocoding cache key: street|city|state|zip
def _contributor_keys(df: pd.DataFrame) -> pd.Series:
    """Cache key per row: street|city|state|zip, NaN as empty, ordinal streets numbered."""
    cols = df[['contributor_street_1', 'contributor_city',
               'contributor_state', 'contributor_zip']].fillna('')
    return (_numbered_streets(cols['contributor_street_1']) + '|' +
            cols['contributor_city'] + '|' +
            cols['contributor_state'] + '|' +
            cols['contributor_zip'])


# _lookup_reason value for a cached town pin that only needs the one-time town re-check
_TOWN_CHECK = "town_check"


# true if a key must be looked up this run
def _needs_lookup(key: str, cache: GeoCache) -> bool:
    return _lookup_reason(key, cache) is not None


# why a key needs another lookup, or None if cached
def _lookup_reason(key: str, cache: GeoCache) -> str | None:
    """Why a key is looked up (again) this run, or None when its cached entry stands."""
    if reviewed_point(key) is not None:
        return None  # checked by hand: the reviewed point is published, no lookup can beat it
    if cache.needs_retry(key):
        return "uncached"

    entry = cache.get(key) or {}
    source = entry.get("source")
    if (entry.get("lat") is not None
            and _reviewed_wrong_street(key, entry, float(entry["lat"]), float(entry["lng"]))):
        return "reviewed_wrong"
    if source == "manual_census":
        return None
    if is_reviewed_zip_typo(key) and source in STREET_LEVEL_SOURCES and entry.get("lat") is not None:
        return None  # checked by hand: the point is right, the filed ZIP is the typo
    if source == "not_found":
        return None if entry.get("validated") else "unvalidated_not_found"
    if is_foreign_key(key):
        # a foreign office cached with a US match is looked up again, internationally
        if entry.get("lat") is not None and (entry.get("country") or "US") == "US":
            return "us_match_for_foreign"
        return None
    if entry.get("country", "US") != "US" or entry.get("lat") is None:
        return None
    if accepted_coordinates(key, entry)[0] is None:
        # a cached point its own address rules out (another state, far outside
        # its ZIP) is never published, so it must be looked up again
        return "rejected"

    street, _city, state, zipcode = key.split("|")
    if state not in _STATE_BOUNDS:
        return None
    lat, lng = float(entry["lat"]), float(entry["lng"])
    # a street-level result outside its ZIP that no run has checked against the
    # filed ZIP and city yet (validated results from before the ZIP check included)
    if _unchecked_outside_zip(key, entry, lat, lng, zipcode):
        return "outside_zip"
    if source == "census":
        return None
    far_from_zip = _far_from_zip(lat, lng, zipcode)
    if entry.get("validated"):
        if source == "nominatim_city" and not is_po_box(street) and far_from_zip:
            return "far_from_zip"
    elif far_from_zip:
        return "far_from_zip"
    if _town_check_due(entry, state, zipcode):
        return _TOWN_CHECK
    return None


# true when a cached town pin needs its one-time re-check
def _town_check_due(entry: dict, state: str, zipcode: str) -> bool:
    """A town-level pin cached before the settlement-only town search, in a ZIP without a centroid to hold it.

    The old free-text query put these on a POI named '... USA', a county, a road or
    the next city (Lego Miniland in Carlsbad for SAN FRANCISCO 94141, North Little
    Rock for LITTLE ROCK 72217), and nothing checked them: a PO-box-only or unique
    ZIP (or none filed) has no centroid for the ZIP rules to compare with. Each is
    looked up once more; the answer is stored with town_checked. A pin in a ZIP with
    a centroid is already held inside that ZIP by prefer_zip_centroids and the
    fallback's ZIP rules, so it is not re-checked.
    """
    return (entry.get("source") == "nominatim_city"
            and not entry.get("town_checked")
            and _zip_point(zipcode, state) is None)


# geocode every unique address in df, skipping already-cached keys
def geocode_addresses(df: pd.DataFrame, cache: GeoCache,
                      batch_size: int = 50):
    """Geocode every unique address in df, skipping cached keys and saving the cache every batch_size lookups."""
    keys_series = _contributor_keys(df)
    keys_series = keys_series[~foreign_address_mask(df)]
    all_keys = set(keys_series[keys_series != '|||'].unique())
    _prefer_zip_centroids_logged(all_keys, cache)

    todo = [key for key in all_keys if _needs_lookup(key, cache)]

    logger.info(f"\n  Unique addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:    {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:         {len(todo):,}")
    if not todo:
        logger.info("  All addresses cached")
        return

    estimated_seconds = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Estimated time:    ~{int(estimated_seconds//3600)}h {int((estimated_seconds%3600)//60)}m")

    _geocode_todo(todo, cache, batch_size)


# replace city centroids with ZIP centroids, logging the count
def _prefer_zip_centroids_logged(keys, cache: GeoCache) -> None:
    changed = prefer_zip_centroids(keys, cache)
    if changed:
        cache.save()
        logger.info(f"  City centroids replaced by the filed ZIP's centroid: {changed:,}")


# map cached results onto latitude/longitude/geocode_level columns
def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Map cached results onto latitude/longitude/geocode_level (vectorized)."""
    keys = _contributor_keys(df)

    # re-validate cached coords against the key's own address (state box, ZIP,
    # foreign office) so a stale wrong cache entry is dropped
    # re-validate one cached key's coordinates against its own address rules
    def _lookup(key):
        entry = cache.get(key)
        lat, lng, level = accepted_coordinates(key, entry)
        if lat is None:
            return np.nan, np.nan, "US", level
        return lat, lng, (entry or {}).get("country", "US"), level

    unique_keys = keys.unique()
    key_results = {key: _lookup(key) for key in unique_keys}

    results = keys.map(key_results)
    df["latitude"] = results.apply(lambda result: result[0])
    df["longitude"] = results.apply(lambda result: result[1])
    df["geocode_level"] = results.apply(lambda result: result[3])
    # a foreign filing (REHOVOT / CA, JERUSALEM, ISRAEL / NY) has no US coordinate
    foreign = foreign_address_mask(df)
    if foreign.any():
        df.loc[foreign, ["latitude", "longitude"]] = np.nan
        df.loc[foreign, "geocode_level"] = "foreign_not_geocoded"
    # x[2] (country) was dropped from the schema; used above only to keep foreign coords

    return df


# run the geocoding engine over todo keys, saving progress
def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    """Run the engine chain over todo keys, saving the cache and logging an ETA every batch_size lookups."""
    stats = {
        "found": 0,
        "failed": 0,
        "transient": 0,
        "city": 0,
        "town_kept": 0,
    }
    start = time.time()
    # the one-time town re-check replaces a working pin only with a better one
    town_rechecks = {key for key in todo if _lookup_reason(key, cache) == _TOWN_CHECK}

    for done, key in enumerate(todo, 1):
        street, city, state, zipcode = key.split("|")
        recheck = key in town_rechecks

        try:
            lat, lng, country, source = _geocode_one(
                street, city, state, zipcode,
            )
        except (NominatimUnavailable, CensusUnavailable) as error:
            if not recheck:  # a re-checked pin stays as it is and is re-checked next run
                cache.put_transient(key)
            stats["transient"] += 1
            logger.debug("Geocoding unavailable for %s: %s", key, error)
            continue

        if lat is not None and not (recheck and (country or "US") != "US"):
            # every result written here passed the ZIP / foreign checks in _geocode_one
            cache.put(key, lat, lng, source, country, validated=True, zip_checked=True,
                      town_checked=True)
            stats["found"] += 1
            if source in {"nominatim_city", "zip_centroid"}:
                stats["city"] += 1
        elif recheck:
            # no town of that name found (JAMAICA NY is no OSM settlement): keep the
            # old pin rather than erase it or move a US filing abroad
            cache.mark_town_checked(key)
            stats["town_kept"] += 1
            logger.debug("Town re-check found nothing better for %s: cached pin kept", key)
        else:
            cache.put_failed(key, validated=True)
            stats["failed"] += 1

        if done % batch_size == 0 or done == len(todo):
            cache.save()
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 1
            eta = (len(todo) - done) / rate
            logger.info(
                "  [%s/%s] found: %s  failed: %s  ETA: %dm %ds",
                f"{done:,}", f"{len(todo):,}",
                f"{stats['found']:,}", f"{stats['failed']:,}",
                int(eta // 60), int(eta % 60),
            )

    cache.save()
    _print_summary(todo, stats, time.time() - start)


# log the final geocoding run summary
def _print_summary(todo: list, stats: dict, elapsed: float) -> None:
    logger.info("\n\n  -- Geocoding Done --")
    logger.info(f"  Processed:   {len(todo):,} in {int(elapsed//60)}m {int(elapsed%60)}s")
    logger.info(f"  Found:       {stats['found']:,}")
    logger.info(f"  Failed:      {stats['failed']:,}")
    if stats.get("transient"):
        logger.info(f"  Retry later: {stats['transient']:,}")
    if stats["city"]:
        logger.info(f"  City-level:  {stats['city']:,}")
    if stats.get("town_kept"):
        logger.info(f"  Town re-check kept the cached pin: {stats['town_kept']:,}")
