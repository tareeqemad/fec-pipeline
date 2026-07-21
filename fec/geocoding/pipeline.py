"""
geocode/pipeline.py — Main geocoding orchestration.

Flow for each unique address:
    PO Box?
        yes → city_level()
        no  → nominatim() → google() → city_level()
"""

import re
import time

import pandas as pd
import numpy as np

from .cache   import GeoCache
from .engines import nominatim, nominatim_international, google, city_level, NOMINATIM_DELAY

from fec.log import get_logger
from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS
logger = get_logger(__name__)


def _in_us_bounds(lat: float, lng: float) -> bool:
    """True if coords fall within ANY US state/territory bounding box (+1° margin).
    The global US guard — distinguishes a real foreign point from a US one even
    when the state field is blank/unknown."""
    for lat_min, lat_max, lng_min, lng_max in _STATE_BOUNDS.values():
        if lat_min - 1 <= lat <= lat_max + 1 and lng_min - 1 <= lng <= lng_max + 1:
            return True
    return False


def _valid_for_state(lat: float, lng: float, state: str) -> bool:
    """Check if coords are plausible for the given US state. Returns True if valid."""
    if not state or state not in _STATE_BOUNDS:
        return _in_us_bounds(lat, lng)  # unknown state: accept only if inside the US
    lat_min, lat_max, lng_min, lng_max = _STATE_BOUNDS[state]
    # Add 1 degree margin for border areas
    return (lat_min - 1 <= lat <= lat_max + 1) and (lng_min - 1 <= lng <= lng_max + 1)


def is_po_box(street) -> bool:
    if pd.isna(street):
        return False
    return bool(re.match(r"^PO\s+BOX", str(street).strip(), re.I))


# ── Main pipeline ──

def geocode_addresses(df: pd.DataFrame, cache: GeoCache,
                      google_key: str | None = None,
                      batch_size: int = 50):
    """
    Geocode every unique address in the DataFrame.

    - Skips addresses already in cache (resume support).
    - Saves cache every `batch_size` addresses.
    - Prints progress as it goes.
    """
    # ── Collect unique address keys (vectorized) ──

    keys_df = df[['contributor_street_1', 'contributor_city',
                  'contributor_state', 'contributor_zip']].fillna('')
    keys_series = (keys_df['contributor_street_1'].str.strip().str.upper() + '|' +
                   keys_df['contributor_city'].str.strip().str.upper() + '|' +
                   keys_df['contributor_state'].str.strip().str.upper() + '|' +
                   keys_df['contributor_zip'].str.strip().str.upper())
    all_keys = set(keys_series[keys_series != '|||'].unique())

    todo = [k for k in all_keys if cache.needs_retry(k)]

    logger.info(f"\n  Unique addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:    {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:         {len(todo):,}")
    if not todo:
        logger.info("  ✓ All addresses cached")
        return

    has_google = bool(google_key)
    est = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Google fallback:   {'on' if has_google else 'off (set GOOGLE_MAPS_API_KEY in .env)'}")
    logger.info(f"  Estimated time:    ~{int(est//3600)}h {int((est%3600)//60)}m")

    # ── Geocode loop ──

    stats = {"found": 0, "failed": 0, "google": 0, "city": 0}
    start = time.time()

    for i, key in enumerate(todo):
        street, city, state, zipcode = key.split("|")

        lat, lng, country, source = _geocode_one(
            street, city, state, zipcode,
            google_key=google_key, has_google=has_google,
        )

        if lat is not None:
            cache.put(key, lat, lng, source, country)
            stats["found"] += 1
            if source == "google":       stats["google"] += 1
            if source == "nominatim_city": stats["city"] += 1
        else:
            cache.put_failed(key)
            stats["failed"] += 1

        # Save + progress
        done = i + 1
        if done % batch_size == 0 or done == len(todo):
            cache.save()
            elapsed = time.time() - start
            rate    = done / elapsed if elapsed > 0 else 1
            eta     = (len(todo) - done) / rate
            logger.info(
                "  [%s/%s] found: %s  failed: %s  ETA: %dm %ds",
                f"{done:,}", f"{len(todo):,}",
                f"{stats['found']:,}", f"{stats['failed']:,}",
                int(eta // 60), int(eta % 60),
            )

    cache.save()
    _print_summary(todo, stats, time.time() - start)


def apply_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """
    Map cached geocoding results → DataFrame columns (vectorized).

    Adds: latitude, longitude, geocode_level
    """
    # Build keys vectorized
    keys_df = df[['contributor_street_1', 'contributor_city',
                  'contributor_state', 'contributor_zip']].fillna('')
    keys = (keys_df['contributor_street_1'].str.strip().str.upper() + '|' +
            keys_df['contributor_city'].str.strip().str.upper() + '|' +
            keys_df['contributor_state'].str.strip().str.upper() + '|' +
            keys_df['contributor_zip'].str.strip().str.upper())

    # Lookup all keys at once
    # Re-validate US coords against the key's state so a stale wrong-state cache
    # entry is dropped; foreign coords (country != US) are kept and carry their
    # country through.
    def _lookup(key):
        result = cache.get(key)
        if result and result["lat"] is not None:
            lat, lng = result["lat"], result["lng"]
            country = result.get("country", "US")
            state = key.split("|")[2] if "|" in key else ""
            if country == "US" and not _valid_for_state(lat, lng, state):
                return np.nan, np.nan, "US", "rejected_out_of_us"
            return lat, lng, country, result["source"]
        return np.nan, np.nan, "US", "not_found"

    # Build unique key → result map (avoid repeated lookups)
    unique_keys = keys.unique()
    key_results = {k: _lookup(k) for k in unique_keys}

    # Map back to rows
    results = keys.map(key_results)
    df["latitude"]      = results.apply(lambda x: x[0])
    df["longitude"]     = results.apply(lambda x: x[1])
    df["geocode_level"] = results.apply(lambda x: x[3])
    # x[2] (country) intentionally not emitted — the column was dropped from the
    # schema; the value is still used above to keep foreign coords as-is.

    return df


# ── Employer geocoding ──


# Methods that use the contributor's own address (no employer geocoding needed)
_SELF_METHODS = {"deterministic_self", "deterministic_no_workplace"}


def geocode_employer_addresses(df: pd.DataFrame, cache: GeoCache,
                               google_key: str | None = None,
                               batch_size: int = 50):
    """
    Geocode employer addresses (from resolve.py output).

    Skips:
      - deterministic_self → uses contributor coords (same address)
      - deterministic_no_workplace → no address
      - Empty employer_address → nothing to geocode

    RETIRED donors with a previous employer (resolve_method != deterministic_self)
    ARE geocoded at the company address.
    """
    if "employer_address" not in df.columns:
        logger.info("  ⚠ No employer_address column — run resolve.py first")
        return

    # Collect unique employer address keys (vectorized filter + key build)
    mask = (df['employer_address'].notna() &
            (df['employer_address'] != '') &
            (~df.get('resolve_method', pd.Series(dtype=str)).fillna('').isin(_SELF_METHODS)))
    emp_df = df.loc[mask, ['employer_address', 'employer_city',
                           'employer_state', 'employer_zip']].fillna('')
    keys = (emp_df['employer_address'].str.strip().str.upper() + '|' +
            emp_df['employer_city'].str.strip().str.upper() + '|' +
            emp_df['employer_state'].str.strip().str.upper() + '|' +
            emp_df['employer_zip'].str.strip().str.upper())
    all_keys = set(keys[keys != '|||'].unique())

    todo = [k for k in all_keys if cache.needs_retry(k)]

    logger.info(f"\n  Employer addresses:  {len(all_keys):,}")
    logger.info(f"  Already cached:      {len(all_keys) - len(todo):,}")
    logger.info(f"  Remaining:           {len(todo):,}")
    if not todo:
        logger.info("  ✓ All employer addresses cached")
        return

    has_google = bool(google_key)
    est = len(todo) * NOMINATIM_DELAY
    logger.info(f"  Google fallback:     {'on' if has_google else 'off'}")
    logger.info(f"  Estimated time:      ~{int(est//3600)}h {int((est%3600)//60)}m")

    stats = {"found": 0, "failed": 0, "google": 0, "city": 0}
    start = time.time()

    for i, key in enumerate(todo):
        street, city, state, zipcode = key.split("|")

        lat, lng, country, source = _geocode_one(
            street, city, state, zipcode,
            google_key=google_key, has_google=has_google,
        )

        if lat is not None:
            cache.put(key, lat, lng, source, country)
            stats["found"] += 1
            if source == "google":         stats["google"] += 1
            if source == "nominatim_city": stats["city"] += 1
        else:
            cache.put_failed(key)
            stats["failed"] += 1

        done = i + 1
        if done % batch_size == 0 or done == len(todo):
            cache.save()
            elapsed = time.time() - start
            rate    = done / elapsed if elapsed > 0 else 1
            eta     = (len(todo) - done) / rate
            logger.info(
                "  [%s/%s] found: %s  failed: %s  ETA: %dm %ds",
                f"{done:,}", f"{len(todo):,}",
                f"{stats['found']:,}", f"{stats['failed']:,}",
                int(eta // 60), int(eta % 60),
            )

    cache.save()
    _print_summary(todo, stats, time.time() - start)


def apply_employer_to_dataframe(df: pd.DataFrame, cache: GeoCache) -> pd.DataFrame:
    """
    Map cached employer geocoding → DataFrame columns (vectorized).

    For deterministic_self (SELF-EMPLOYED, RETIRED without previous employer):
        copies contributor lat/lng (same address).
    For real employers:
        looks up in cache.

    Adds: employer_latitude, employer_longitude, employer_geocode_level
    """
    method = df.get('resolve_method', pd.Series(dtype=str)).fillna('')

    # Start with NaN for everything
    df["employer_latitude"]      = np.nan
    df["employer_longitude"]     = np.nan
    df["employer_geocode_level"] = "no_address"

    # ── Case 1: deterministic_self → copy contributor coords ──
    mask_self = method == "deterministic_self"
    df.loc[mask_self, "employer_latitude"]      = df.loc[mask_self, "latitude"]
    df.loc[mask_self, "employer_longitude"]     = df.loc[mask_self, "longitude"]
    df.loc[mask_self, "employer_geocode_level"] = "contributor_copy"

    # ── Case 2: no_workplace/unresolved → keep no_address ──
    mask_skip = method.isin({"deterministic_no_workplace", "unresolved", "fec_not_found"})
    # Already set to no_address above

    # ── Case 3: real employer addresses → cache lookup ──
    emp_addr = df.get('employer_address', pd.Series(dtype=str)).fillna('')
    mask_real = (~mask_self) & (~mask_skip) & (emp_addr != '')

    if mask_real.any():
        real_df = df.loc[mask_real, ['employer_address', 'employer_city',
                                     'employer_state', 'employer_zip']].fillna('')
        keys = (real_df['employer_address'].str.strip().str.upper() + '|' +
                real_df['employer_city'].str.strip().str.upper() + '|' +
                real_df['employer_state'].str.strip().str.upper() + '|' +
                real_df['employer_zip'].str.strip().str.upper())

        # Build unique key → result map
        unique_keys = keys.unique()
        key_results = {}
        for k in unique_keys:
            result = cache.get(k)
            if result and result["lat"] is not None:
                key_results[k] = (result["lat"], result["lng"], result["source"])
            else:
                key_results[k] = (np.nan, np.nan, "not_found")

        results = keys.map(key_results)
        df.loc[mask_real, "employer_latitude"]      = results.apply(lambda x: x[0]).values
        df.loc[mask_real, "employer_longitude"]     = results.apply(lambda x: x[1]).values
        df.loc[mask_real, "employer_geocode_level"] = results.apply(lambda x: x[2]).values

    return df


# ── Internal ──

_SUITE_RE = re.compile(
    # A trailing suite / floor / unit token. REQUIRE whitespace before the
    # keyword and a word boundary after it, so the short abbreviations
    # (FL, STE, RM, APT…) only match as standalone tokens ("STE 225", "FL 3"),
    # never as a substring inside a street name (FLANDERS, WEBSTER, MAYFLOWER,
    # FARMSTEAD must survive — they were being mangled into garbage queries).
    r',?\s+(?:Suite|Ste|Floor|Fl|Unit|Apt|Apartment|Room|Rm|Bldg|PH)\b\.?\s*\S+.*$'
    r'|,?\s*#\s*\S+.*$',
    re.IGNORECASE,
)
_FLOOR_RE = re.compile(
    r',?\s*\d+(?:st|nd|rd|th)\s+Floor.*$',
    re.IGNORECASE,
)


def _clean_street_for_geocoding(street: str) -> str:
    """Strip suite/floor/unit suffixes that confuse Nominatim.
    '200 Park Ave, 17th Floor' -> '200 Park Ave'
    '410 17th St #2200' -> '410 17th St'
    """
    s = _FLOOR_RE.sub('', street)
    s = _SUITE_RE.sub('', s)
    return s.strip().rstrip(',')


def _geocode_one(street, city, state, zipcode, *,
                 google_key=None, has_google=False):
    """
    Geocode a single address through the engine chain.
    Validates results against state bounding box to catch wrong-state errors
    (e.g. Nominatim returning Virgin Islands coords for Jefferson, LA).

    If every US attempt fails, falls back to an international lookup so a
    foreign address mislabeled with a US state (e.g. an Israeli street typed as
    "NY") is kept and flagged — not silently dropped.

    Returns (lat, lng, country, source) or (None, None, None, 'not_found').
    """
    if not city and not state:
        return None, None, None, "not_found"

    # PO Box → city level directly
    if is_po_box(street):
        lat, lng, cc = city_level(city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat and _valid_for_state(lat, lng, state):
            return lat, lng, cc or "US", "nominatim_city"
        return _geocode_international(street, city, zipcode)

    # Strip suite/floor/unit info that confuses geocoders
    if street:
        street = _clean_street_for_geocoding(street)

    # Engine 1: Nominatim (street-level)
    if street:
        lat, lng, cc = nominatim(street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat and _valid_for_state(lat, lng, state):
            return lat, lng, cc or "US", "nominatim"
        elif lat:
            logger.debug("Nominatim result lat=%.4f lng=%.4f rejected — outside %s", lat, lng, state)

    # Engine 2: Google fallback
    if has_google and street:
        lat, lng, cc = google(street, city, state, zipcode, google_key)
        if lat and _valid_for_state(lat, lng, state):
            return lat, lng, cc or "US", "google"

    # Engine 3: City-level fallback
    lat, lng, cc = city_level(city, state, zipcode)
    time.sleep(NOMINATIM_DELAY)
    if lat and _valid_for_state(lat, lng, state):
        return lat, lng, cc or "US", "nominatim_city"

    # Engine 4: International — the address may simply be foreign.
    return _geocode_international(street, city, zipcode)


def _geocode_international(street, city, zipcode):
    """Last resort: geocode WITHOUT a US restriction. Accept the result ONLY
    when it's confidently foreign — a non-US country AND coords outside the US.
    Otherwise it's just an un-geocodable US address, not a foreign one, so we
    return not_found rather than risk a bad pin. The (suspected-wrong) US state
    is dropped from the query so it doesn't bias the lookup back into the US."""
    if not street and not city:
        return None, None, None, "not_found"
    lat, lng, cc = nominatim_international(street, city, "", "")
    time.sleep(NOMINATIM_DELAY)
    if lat and cc and cc != "US" and not _in_us_bounds(lat, lng):
        return lat, lng, cc, "nominatim_intl"
    return None, None, None, "not_found"


def _print_summary(todo: int, stats: dict, elapsed: float) -> None:
    logger.info(f"\n\n  ── Geocoding Done ──")
    logger.info(f"  Processed:   {len(todo):,} in {int(elapsed//60)}m {int(elapsed%60)}s")
    logger.info(f"  Found:       {stats['found']:,}")
    logger.info(f"  Failed:      {stats['failed']:,}")
    if stats["google"]:
        logger.info(f"  Via Google:  {stats['google']:,}")
    if stats["city"]:
        logger.info(f"  City-level:  {stats['city']:,}")
