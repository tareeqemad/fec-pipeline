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


# Apply city formatting and configured corrections.
def clean_cities(df: pd.DataFrame) -> pd.DataFrame:
    # Apply places recovered from the street before city rules.
    _apply_house_number_zip(df)

    cities = _strip_city_text(df['contributor_city'])
    states = df['contributor_state'].fillna('').astype(str).str.strip().str.upper()
    # ZIP5 from raw data; ZIP cleaning runs after city fixes.
    zip5 = _clean_zip_raw(df['contributor_zip'])
    before = cities.copy()
    cities = _table_city_fixes(cities, states, zip5)
    table_fixed = before.fillna('') != cities.fillna('')
    df['contributor_city'] = cities

    df[CITY_TABLE_FIXED] = table_fixed

    # Expand ST./MT./FT. after table corrections.
    df['contributor_city'] = df['contributor_city'].map(expand_city_abbreviations)

    return df


# Strip ZIPs, states, units and punctuation.
def _strip_city_text(raw: pd.Series) -> pd.Series:
    cities = raw.astype(str).str.strip().str.upper()

    cities = cities.str.lstrip('`~')

    # Strip trailing ZIPs and states, as in "BETHESDA, MARYLAND 20817".
    cities = cities.str.replace(r',?\s*\d{5}(-\d{4})?\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+[A-Z]{2}\s+\d{5}\s*$', '', regex=True)
    cities = cities.str.replace(r',?\s+(MARYLAND|CALIFORNIA|TEXAS)\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+D\.C\.,?\s*(USA)?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r',?\s+PA\.?\s*\d*$', '', regex=True)
    cities = cities.str.replace(r'/[A-Z]+$', '', regex=True)

    # Unit labels are not cities.
    cities = cities.where(~cities.str.match(r'^APT\s|^STE\s|^UNIT\s|^#\d', case=False, na=False), other=np.nan)

    cities = cities.str.rstrip('.,;:')

    # Remove state codes stuck to the city name.
    cities = cities.str.replace(STATE_IN_CITY, '', regex=True).str.strip()

    return cities.str.replace(r'\s{2,}', ' ', regex=True)


# Apply curated city corrections.
def _table_city_fixes(cities: pd.Series, states: pd.Series, zip5) -> pd.Series:
    cities = cities.replace(CITY_NORMALIZE)
    state_fixes = pd.Series(
        list(zip(cities, states)), index=cities.index
    ).map(CITY_STATE_NORMALIZE)
    cities = state_fixes.fillna(cities)
    if zip5 is not None:
        # ZIP-dependent abbreviations: 'NY' is NEW YORK only in Manhattan.
        zip3 = zip5.fillna('').astype(str).str[:3]
        zip_fixes = pd.Series(
            [CITY_ZIP3_NORMALIZE.get(key) for key in zip(cities, states, zip3)],
            index=cities.index, dtype=object,
        )
        cities = zip_fixes.fillna(cities)
    return cities
