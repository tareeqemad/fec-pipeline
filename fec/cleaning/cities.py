"""Clean city names: table fixes, place qualifiers, detected typos."""
import difflib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from fec.cleaning.zips import _clean_zip_raw
from fec.cleaning.house_number_zip import (
    _apply_house_number_zip,
)
from fec.config.cities import (
    CITY_NORMALIZE,
    CITY_STATE_NORMALIZE,
    CITY_ZIP3_NORMALIZE,
    expand_city_abbreviations,
)
from fec.config.streets import (
    STATE_IN_CITY,
)

# real-city lookalikes that must never be "corrected" into each other
_PROTECTED_CITIES = {
    'MEDFORD', 'BEDFORD',
    'WEST WINDSOR', 'EAST WINDSOR',
    'SANTA CLARA', 'SANTA CLARITA',
    'HOPKINSVILLE', 'TOMPKINSVILLE',
    'WEST BLOOMFIELD TOWNSHIP', 'BLOOMFIELD TOWNSHIP',
    'ST. LOUIS PARK', 'ST LOUIS PARK',
    'NORTH CAMBRIDGE', 'CAMBRIDGE',
}


# internal flag: the row's city was rewritten from a typo/abbreviation table or
# the fuzzy pass, i.e. guessed rather than filed; the same-street recovery lets
# such a guess yield to the same home's own city at the same ZIP
CITY_TABLE_FIXED = '_city_table_fixed'


# words that name a different place when swapped, added or dropped
# (EAST HARTFORD / WEST HARTFORD): never a typo, whatever the string ratio
_PLACE_QUALIFIERS = frozenset({
    'NORTH', 'SOUTH', 'EAST', 'WEST',
    'NORTHEAST', 'NORTHWEST', 'SOUTHEAST', 'SOUTHWEST',
    'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW',
    'UPPER', 'LOWER', 'NEW', 'OLD', 'GREAT', 'LITTLE',
})


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
def clean_cities(df: pd.DataFrame, fuzzy: bool = True, report_dir: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Clean city names; report_dir writes auto_city_fixes.json for review. Returns (df, counts)."""
    counts = {'known_fixes': 0, 'fuzzy_fixes': 0, 'punctuation_cleaned': 0}

    # city/state/ZIP that clean_streets read from a street whose ZIP box held
    # the house number (counted there); written first so the tables below see
    # the real place
    _apply_house_number_zip(df)

    cities, counts['punctuation_cleaned'] = _strip_city_text(df['contributor_city'])
    states = df['contributor_state'].fillna('').astype(str).str.strip().str.upper()
    zip5 = _zip5(df)
    before = cities.copy()
    cities = _table_city_fixes(cities, states, zip5)
    counts['known_fixes'] = _changed(before, cities)
    table_fixed = before.fillna('') != cities.fillna('')
    df['contributor_city'] = cities

    if fuzzy:
        counts['fuzzy_fixes'], fuzzy_fixed = _fix_detected_city_typos(df, states, zip5, report_dir)
        table_fixed |= fuzzy_fixed
    df[CITY_TABLE_FIXED] = table_fixed

    # expand ST./MT./FT. abbreviations LAST so the fuzzy pass above still
    # matches its "ST."-form protected list
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


# apply the fuzzy-detected typo fixes and optionally log them
def _fix_detected_city_typos(df: pd.DataFrame, states: pd.Series, zip5, report_dir) -> tuple[int, pd.Series]:
    """Returns (rows changed, mask of rows a fix applied to)."""
    auto_fixes = _auto_detect_city_typos(df)
    changed, fixed_rows = 0, pd.Series(False, index=df.index)
    if auto_fixes:
        # a fix learned in one state or at one ZIP never renames the same spelling anywhere else
        fixed = pd.Series(
            [auto_fixes.get(key) for key in zip(states, df['contributor_city'], zip5)],
            index=df.index, dtype=object,
        )
        before = df['contributor_city'].copy()
        df['contributor_city'] = fixed.fillna(df['contributor_city'])
        changed = _changed(before, df['contributor_city'])
        fixed_rows = fixed.notna()

    if report_dir:
        report = [
            {'state': state, 'city': city, 'zip5': zip_code, 'fix': fix}
            for (state, city, zip_code), fix in sorted(auto_fixes.items())
        ]
        report_path = Path(report_dir) / 'auto_city_fixes.json'
        with open(report_path, 'w', encoding='utf-8') as handle:
            json.dump(report, handle, indent=2, ensure_ascii=False)
    return changed, fixed_rows


# true when two names differ only by qualifier words
def _swaps_place_qualifier(city: str, other: str) -> bool:
    """True when the two names differ only by whole qualifier words (EAST HARTFORD / WEST HARTFORD, WEST BLOOMFIELD TOWNSHIP / BLOOMFIELD TOWNSHIP)."""
    words, other_words = city.split(), other.split()
    only_city = [word for word in words if word not in other_words]
    only_other = [word for word in other_words if word not in words]
    differing = only_city + only_other
    return bool(differing) and all(word in _PLACE_QUALIFIERS for word in differing)


# find city typos by fuzzy-matching rare names to common ones
def _auto_detect_city_typos(df: pd.DataFrame, cutoff: float = 0.88, min_common: int = 10, max_rare: int = 3) -> dict:
    """Fuzzy-match rare city names against common ones in the same state; returns {(state, typo, zip5): fix}.

    String similarity alone renames one real town into its neighbour (EAST
    HARTFORD 06128 -> WEST HARTFORD), so a pair is accepted only per ZIP5 that
    is also filed with the common city in the same state by at least one other
    row (the donor's own other filings count), and never when the names differ
    by a place-qualifier word. Rows of the rare name at any other ZIP stay as filed.
    """
    zip5 = _zip5(df)
    if zip5 is None:
        return {}
    frame = pd.DataFrame({
        'state': df['contributor_state'].fillna('').astype(str).str.strip().str.upper(),
        'city': df['contributor_city'],
        'zip5': zip5,
    })
    frame = frame[(frame['state'] != '') & frame['city'].notna()]
    city_counts = frame.groupby(['state', 'city']).size().reset_index(name='n')
    filed = set(
        frame.dropna(subset=['zip5'])[['state', 'city', 'zip5']]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    rare_zips = (
        frame.dropna(subset=['zip5']).groupby(['state', 'city'])['zip5'].unique().to_dict()
    )
    known = set(CITY_NORMALIZE) | {city for city, _state in CITY_STATE_NORMALIZE} \
        | {city for city, _state, _zip3 in CITY_ZIP3_NORMALIZE}

    fixes = {}
    for state, state_cities in city_counts.groupby('state', sort=False):
        common_names = state_cities[state_cities['n'] >= min_common]['city'].tolist()
        rare_cities = state_cities[state_cities['n'] <= max_rare]

        for city in rare_cities['city']:
            if city in _PROTECTED_CITIES or city in known:
                continue

            matches = difflib.get_close_matches(city, common_names, n=1, cutoff=cutoff)
            if not matches or matches[0] == city:
                continue
            target = matches[0]
            if _swaps_place_qualifier(city, target):
                continue
            for zip_code in rare_zips.get((state, city), ()):
                if (state, target, zip_code) in filed:
                    fixes[(state, city, zip_code)] = target

    return fixes
