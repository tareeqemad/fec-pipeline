"""Geocode employer addresses and copy the points onto employer rows."""
import numpy as np
import pandas as pd

from fec.geocoding.accepted import (
    accepted_coordinates,
)
from fec.geocoding.cache import GeoCache
from fec.geocoding.engines import (
    NOMINATIM_DELAY,
)
from fec.geocoding.pipeline import (
    _geocode_todo,
    _needs_lookup,
    _prefer_zip_centroids_logged,
)
from fec.geocoding.street_text import _numbered_streets
from fec.log import get_logger

logger = get_logger(__name__)


# build a normalized cache key per employer address row
def _employer_keys(frame: pd.DataFrame) -> pd.Series:
    """Cache key per row: STREET|CITY|STATE|ZIP, stripped, uppercased, ordinal streets numbered."""
    cols = frame[['employer_address', 'employer_city',
                  'employer_state', 'employer_zip']].fillna('')
    return (_numbered_streets(cols['employer_address'].str.strip().str.upper()) + '|' +
            cols['employer_city'].str.strip().str.upper() + '|' +
            cols['employer_state'].str.strip().str.upper() + '|' +
            cols['employer_zip'].str.strip().str.upper())


# geocode employer addresses not already cached
def geocode_employer_addresses(df: pd.DataFrame, cache: GeoCache,
                               batch_size: int = 50):
    """Geocode employer addresses; skips empty rows, but RETIRED with a previous employer IS geocoded at the company address."""
    mask = (df['employer_address'].notna() &
            (df['employer_address'] != ''))
    keys = _employer_keys(df.loc[mask])
    all_keys = set(keys[keys != '|||'].unique())
    _prefer_zip_centroids_logged(all_keys, cache)

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


# fill lat/lng/geocode level from cached employer geocoding
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
            lat, lng, level = accepted_coordinates(key, cache.get(key))
            if lat is None:
                key_results[key] = (np.nan, np.nan, level)
            else:
                key_results[key] = (lat, lng, level)

        results = keys.map(key_results)
        df.loc[mask_real, "employer_latitude"] = results.apply(lambda result: result[0]).values
        df.loc[mask_real, "employer_longitude"] = results.apply(lambda result: result[1]).values
        df.loc[mask_real, "employer_geocode_level"] = results.apply(lambda result: result[2]).values

    return df
