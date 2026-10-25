"""Format city names and apply the configured corrections."""

import numpy as np
import pandas as pd

from fec.cleaning.addresses.house_number_zip import (
    _apply_house_number_zip,
)
from fec.cleaning.addresses.zips import _clean_zip_raw
from fec.config.cities import (
    CITY_NORMALIZE,
    CITY_STATE_NORMALIZE,
    CITY_ZIP3_NORMALIZE,
    expand_city_abbreviations,
)
from fec.config.streets import (
    STATE_IN_CITY,
)

# Mark table corrections for the later same-street comparison.
CITY_TABLE_FIXED = '_city_table_fixed'


# count rows where a value actually differs, ignoring blank-to-blank
def _changed(before: pd.Series, after: pd.Series) -> int:
    """Rows whose value changed; a blank that stays blank is not a change."""
    return int((before.fillna('') != after.fillna('')).sum())


# derive cleaned ZIP5 from the raw contributor_zip column, if present
def _zip5(df: pd.DataFrame) -> pd.Series | None:
    """ZIP5 derived from the raw contributor_zip (clean_zips runs after the cities); None without that column."""
    if 'contributor_zip' not in df.columns:
        return None
    zip5, _n_invalid = _clean_zip_raw(df['contributor_zip'])
    return zip5


# run all city-cleaning steps and return updated counts
def clean_cities(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Apply formatting and table corrections; return their counts."""
    counts = {'known_fixes': 0, 'punctuation_cleaned': 0}

    # city/state/ZIP that clean_streets read from a street whose ZIP box held
    # the house number (counted there); written first so the tables below see
    # the real place
    _apply_house_number_zip(df)

    cities, counts['punctuation_cleaned'] = _strip_city_text(df['contributor_city'])
    states = df['contributor_state'].fillna('').astype(str).str.strip().str.upper()
    zip5 = _zip5(df)
    before = cities.copy()
    cities = _table_city_fixes(cities, states, zip5)
    table_fixed = before.fillna('') != cities.fillna('')
    counts['known_fixes'] = int(table_fixed.sum())
    df['contributor_city'] = cities

    df[CITY_TABLE_FIXED] = table_fixed

    # Expand ST./MT./FT. after table corrections.
    df['contributor_city'] = df['contributor_city'].map(expand_city_abbreviations)

    return df, counts


# strip ZIPs, states, units and punctuation from the city text
def _strip_city_text(raw: pd.Series) -> tuple[pd.Series, int]:
    """Returns (cities, rows whose punctuation or trailing state was removed)."""
    cities = raw.astype(str).str.strip().str.upper()

    cities = cities.str.lstrip('`~')

    # embedded ZIPs and state/city combos ("BETHESDA, MARYLAND 20817", "CHARLOTTE, NC 28226")
    cities = cities.str.replace(r',?\s*\d{5}(-\d{4})?\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+[A-Z]{2}\s+\d{5}\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+(MARYLAND|CALIFORNIA|TEXAS)\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+D\.C\.,?\s*(USA)?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+PA\.?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r'/[A-Z]+$', '', regex=True)

    # a unit string is not a city
    cities = cities.where(~cities.str.match(r'^APT\s|^STE\s|^UNIT\s|^#\d', case=False, na=False), other=np.nan)

    before = cities.copy()
    cities = cities.str.rstrip('.,;:')
    cleaned = _changed(before, cities)

    # state codes stuck at end of city
    before = cities.copy()
    cities = cities.str.replace(STATE_IN_CITY, '', regex=True).str.strip()
    cleaned += _changed(before, cities)

    return cities.str.replace(r'\s{2,}', ' ', regex=True), cleaned


# fix city names using the hand-curated lookup tables
def _table_city_fixes(cities: pd.Series, states: pd.Series, zip5) -> pd.Series:
    cities = cities.replace(CITY_NORMALIZE)
    state_fixes = pd.Series(
        list(zip(cities, states)), index=cities.index
    ).map(CITY_STATE_NORMALIZE)
    cities = state_fixes.fillna(cities)
    if zip5 is not None:
        # short forms whose city depends on the ZIP ('NY' is NEW YORK only in Manhattan)
        zip3 = zip5.fillna('').astype(str).str[:3]
        zip_fixes = pd.Series(
            [CITY_ZIP3_NORMALIZE.get(key) for key in zip(cities, states, zip3)],
            index=cities.index, dtype=object,
        )
        cities = zip_fixes.fillna(cities)
    return cities
