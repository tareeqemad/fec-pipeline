"""Address geocoding: the single-address engine chain plus the contributor and employer dataframe passes."""

import math
import re
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from fec.log import get_logger
from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS

from .cache import GeoCache
from .engines import (
    CensusUnavailable,
    NOMINATIM_DELAY,
    NominatimUnavailable,
    census,
    city_level,
    nominatim,
    nominatim_international,
)

logger = get_logger(__name__)

_ZIP_CENTROIDS = Path(__file__).resolve().parents[2] / "data" / "database" / "zip_centroids.csv"
_ZIP_OUTLIER_KM = 50

_PO_BOX_RE = re.compile(r"^PO\s+BOX", re.IGNORECASE)

# require whitespace before the keyword and a word boundary after, so short
# abbreviations (FL, STE, RM, APT) never match inside street names like FLANDERS
_SUITE_RE = re.compile(
    r',?\s+(?:Suite|Ste|Floor|Fl|Unit|Apt|Apartment|Room|Rm|Bldg|PH)\b\.?\s*\S+.*$'
    r'|,?\s*#\s*\S+.*$',
    re.IGNORECASE,
)
_FLOOR_RE = re.compile(
    r',?\s*\d+(?:st|nd|rd|th)\s+Floor.*$',
    re.IGNORECASE,
)


def _in_us_bounds(lat: float, lng: float) -> bool:
    """True if coords fall in any US state/territory bbox (+1 deg margin) -- the global US guard."""
    for lat_min, lat_max, lng_min, lng_max in _STATE_BOUNDS.values():
        if lat_min - 1 <= lat <= lat_max + 1 and lng_min - 1 <= lng <= lng_max + 1:
            return True
    return False


def _valid_for_state(lat: float, lng: float, state: str) -> bool:
    """True if coords are plausible for the given US state (1 deg border margin)."""
    if not state or state not in _STATE_BOUNDS:
        return _in_us_bounds(lat, lng)  # unknown state: accept only if inside the US
    lat_min, lat_max, lng_min, lng_max = _STATE_BOUNDS[state]
    return (lat_min - 1 <= lat <= lat_max + 1) and (lng_min - 1 <= lng <= lng_max + 1)


def is_po_box(street: str) -> bool:
    return bool(_PO_BOX_RE.match(street.strip()))


def _contributor_keys(df: pd.DataFrame) -> pd.Series:
    """Cache key per row: street|city|state|zip, NaN as empty."""
    cols = df[['contributor_street_1', 'contributor_city',
               'contributor_state', 'contributor_zip']].fillna('')
    return (cols['contributor_street_1'] + '|' +
            cols['contributor_city'] + '|' +
            cols['contributor_state'] + '|' +
            cols['contributor_zip'])


def _employer_keys(frame: pd.DataFrame) -> pd.Series:
    """Cache key per row: STREET|CITY|STATE|ZIP, stripped and uppercased."""
    cols = frame[['employer_address', 'employer_city',
                  'employer_state', 'employer_zip']].fillna('')
    return (cols['employer_address'].str.strip().str.upper() + '|' +
            cols['employer_city'].str.strip().str.upper() + '|' +
            cols['employer_state'].str.strip().str.upper() + '|' +
            cols['employer_zip'].str.strip().str.upper())


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    if not _ZIP_CENTROIDS.exists():
        return {}
    rows = pd.read_csv(_ZIP_CENTROIDS, dtype={"zip": str})
    return {
        str(row.zip).zfill(5): (float(row.lat), float(row.lng))
        for row in rows.itertuples()
    }


def _distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, first)
    lat2, lng2 = map(math.radians, second)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(value))


def _far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    if not re.fullmatch(r"\d{5}", zipcode):
        return False
    centroid = _zip_centroids().get(zipcode)
    return bool(centroid and _distance_km((lat, lng), centroid) > _ZIP_OUTLIER_KM)


def _needs_lookup(key: str, cache: GeoCache) -> bool:
    if cache.needs_retry(key):
        return True

    entry = cache.get(key) or {}
    source = entry.get("source")
    if source in {"census", "manual_census"}:
        return False
    if source == "not_found":
        return not entry.get("validated")
    if entry.get("country", "US") != "US" or entry.get("lat") is None:
        return False

    street, _city, state, zipcode = key.split("|")
    if state not in _STATE_BOUNDS:
        return False
    far_from_zip = _far_from_zip(float(entry["lat"]), float(entry["lng"]), zipcode)
    if entry.get("validated"):
        return source == "nominatim_city" and not is_po_box(street) and far_from_zip
    if not far_from_zip:
        return False
    return True


def geocode_addresses(df: pd.DataFrame, cache: GeoCache,
                      batch_size: int = 50):
    """Geocode every unique address in df, skipping cached keys and saving the cache every batch_size lookups."""
    keys_series = _contributor_keys(df)
    all_keys = set(keys_series[keys_series != '|||'].unique())

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


def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Map cached results onto latitude/longitude/geocode_level (vectorized)."""
    keys = _contributor_keys(df)

    # re-validate US coords against the key's state so a stale wrong-state cache
    # entry is dropped; foreign coords (country != US) are kept as-is
    def _lookup(key):
        result = cache.get(key)
        if result and result["lat"] is not None:
            lat, lng = result["lat"], result["lng"]
            country = result.get("country", "US")
            state = key.split("|")[2]
            if country == "US" and not _valid_for_state(lat, lng, state):
                return np.nan, np.nan, "US", "rejected_out_of_us"
            return lat, lng, country, result["source"]
        return np.nan, np.nan, "US", "not_found"

    unique_keys = keys.unique()
    key_results = {key: _lookup(key) for key in unique_keys}

    results = keys.map(key_results)
    df["latitude"] = results.apply(lambda result: result[0])
    df["longitude"] = results.apply(lambda result: result[1])
    df["geocode_level"] = results.apply(lambda result: result[3])
    # x[2] (country) was dropped from the schema; used above only to keep foreign coords

    return df


def geocode_employer_addresses(df: pd.DataFrame, cache: GeoCache,
                               batch_size: int = 50):
    """Geocode employer addresses; skips empty rows, but RETIRED with a previous employer IS geocoded at the company address."""
    mask = (df['employer_address'].notna() &
            (df['employer_address'] != ''))
    keys = _employer_keys(df.loc[mask])
    all_keys = set(keys[keys != '|||'].unique())

    todo = [key for key in all_keys if _needs_lookup(key, cache)]

    logger.info(f"\n  Employer addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:      {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:           {len(todo):,}")
    if not todo:
        logger.info("  All employer addresses cached")
        return

    estimated_seconds = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Estimated time:      ~{int(estimated_seconds//3600)}h {int((estimated_seconds%3600)//60)}m")

    _geocode_todo(todo, cache, batch_size)


def apply_employer_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """Map cached employer geocoding onto employer_latitude/longitude/geocode_level."""
    method = df.get(
        "resolve_method",
        pd.Series("", index=df.index),
    ).fillna("")

    df["employer_latitude"] = np.nan
    df["employer_longitude"] = np.nan
    df["employer_geocode_level"] = "no_address"

    # fec_not_found rows keep no_address
    mask_skip = method == "fec_not_found"

    employer_address = df['employer_address'].fillna('')
    mask_real = (~mask_skip) & (employer_address != '')

    if mask_real.any():
        keys = _employer_keys(df.loc[mask_real])

        unique_keys = keys.unique()
        key_results = {}
        for key in unique_keys:
            result = cache.get(key)
            if result and result["lat"] is not None:
                key_results[key] = (result["lat"], result["lng"], result["source"])
            else:
                key_results[key] = (np.nan, np.nan, "not_found")

        results = keys.map(key_results)
        df.loc[mask_real, "employer_latitude"] = results.apply(lambda result: result[0]).values
        df.loc[mask_real, "employer_longitude"] = results.apply(lambda result: result[1]).values
        df.loc[mask_real, "employer_geocode_level"] = results.apply(lambda result: result[2]).values

    return df


def _geocode_todo(todo: list, cache: GeoCache, batch_size: int) -> None:
    """Run the engine chain over todo keys, saving the cache and logging an ETA every batch_size lookups."""
    stats = {
        "found": 0,
        "failed": 0,
        "transient": 0,
        "city": 0,
    }
    start = time.time()

    for done, key in enumerate(todo, 1):
        street, city, state, zipcode = key.split("|")

        try:
            lat, lng, country, source = _geocode_one(
                street, city, state, zipcode,
            )
        except NominatimUnavailable as error:
            cache.put_transient(key)
            stats["transient"] += 1
            logger.debug("Geocoding unavailable for %s: %s", key, error)
            continue

        if lat is not None:
            cache.put(key, lat, lng, source, country, validated=True)
            stats["found"] += 1
            if source == "nominatim_city":
                stats["city"] += 1
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


def _clean_street_for_geocoding(street: str) -> str:
    """Strip suite/floor/unit suffixes that confuse Nominatim."""
    cleaned = _FLOOR_RE.sub('', street)
    cleaned = _SUITE_RE.sub('', cleaned)
    return cleaned.strip().rstrip(',')


def _city_fallback(city: str, state: str, zipcode: str,
                   require_zip_match: bool = False) -> tuple:
    """Retry city without ZIP."""
    zip_codes = [zipcode]
    if zipcode:
        zip_codes.append("")

    for zip_code in zip_codes:
        lat, lng, country = city_level(city, state, zip_code)
        time.sleep(NOMINATIM_DELAY)
        if not lat or not _valid_for_state(lat, lng, state):
            continue
        if require_zip_match and _far_from_zip(lat, lng, zipcode):
            continue
        return lat, lng, country

    return None, None, None


def _geocode_one(street, city, state, zipcode):
    """Geocode one address."""
    if not city and not state:
        return None, None, None, "not_found"

    if is_po_box(street):
        lat, lng, country_code = _city_fallback(city, state, zipcode)
        if lat:
            return lat, lng, country_code or "US", "nominatim_city"
        return _geocode_international(street, city)

    if street:
        street = _clean_street_for_geocoding(street)

    if street and state in _STATE_BOUNDS:
        try:
            lat, lng, country_code = census(street, city, state, zipcode)
        except CensusUnavailable as error:
            logger.debug("Census Geocoder unavailable for %s: %s", street, error)
        else:
            if lat and _valid_for_state(lat, lng, state):
                return lat, lng, country_code or "US", "census"

    if street:
        lat, lng, country_code = nominatim(street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat and _valid_for_state(lat, lng, state):
            return lat, lng, country_code or "US", "nominatim"
        if lat:
            logger.debug("Nominatim result lat=%.4f lng=%.4f rejected - outside %s", lat, lng, state)

    lat, lng, country_code = _city_fallback(
        city, state, zipcode, require_zip_match=True,
    )
    if lat:
        return lat, lng, country_code or "US", "nominatim_city"

    # a foreign address mislabeled with a US state is kept and flagged, not dropped
    return _geocode_international(street, city)


def _geocode_international(street, city):
    """Last resort without a US restriction; accept only a confidently foreign result (non-US country AND coords outside the US), else not_found."""
    if not street and not city:
        return None, None, None, "not_found"
    # the suspected-wrong US state is dropped so it doesn't bias the lookup back into the US
    lat, lng, country_code = nominatim_international(street, city, "", "")
    time.sleep(NOMINATIM_DELAY)
    if lat and country_code and country_code != "US" and not _in_us_bounds(lat, lng):
        return lat, lng, country_code, "nominatim_intl"
    return None, None, None, "not_found"


def _print_summary(todo: list, stats: dict, elapsed: float) -> None:
    logger.info("\n\n  -- Geocoding Done --")
    logger.info(f"  Processed:   {len(todo):,} in {int(elapsed//60)}m {int(elapsed%60)}s")
    logger.info(f"  Found:       {stats['found']:,}")
    logger.info(f"  Failed:      {stats['failed']:,}")
    if stats.get("transient"):
        logger.info(f"  Retry later: {stats['transient']:,}")
    if stats["city"]:
        logger.info(f"  City-level:  {stats['city']:,}")
