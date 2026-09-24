"""Contributor street, city, and ZIP normalization."""
import difflib
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from fec.config.cities import (
    CITY_NORMALIZE,
    CITY_STATE_NORMALIZE,
    CITY_ZIP3_NORMALIZE,
    expand_city_abbreviations,
)
from fec.config.streets import (
    POBOX_RE, DIR_PREFIX, DIR_SUFFIX, DIR_MID, STREET_TYPES,
    UNIT_RULES, UNIT_EXTRACT, HASH_EXTRACT, STREET_TYPO_RULES,
    STATE_IN_CITY, FLOOR_ONLY_RE,
)

# Street cleaning

# placeholder values that mean "no address"
_JUNK_STREETS = {'HOME', 'YES', 'NO', 'SAME', 'N/A', 'NA', 'NONE',
                 'UNKNOWN', 'X', 'XX', 'XXX', 'NOT PROVIDED',
                 'UNITED STATES OF AMERICA', 'USA'}
# the same placeholders in street_2, except a bare X: "UNIT X" is written "X" there
_JUNK_UNITS = (_JUNK_STREETS - {'X'}) | {'NULL', 'N.A', 'N.A.'}


def _has_no_unit_text(value: str) -> bool:
    """True for a street_2 holding no letter or digit ('.', '-', '..'); a bare '#' is kept for address_review, which reports it as a unit keyword without a number."""
    return not any(char.isalnum() or char == '#' for char in value)


# trailing unit word without a number
_TRAILING_UNIT_RE = re.compile(r'\s+(?:APT|UNIT|STE|SUITE)\s*$')
_INVALID_STREET_RE = re.compile(r'^(?:C\d{7,}|\d+)$')
_SHORT_POBOX_RE = re.compile(r'^(?:P\.?\s*O\.?\s*B?|BOX)\s+(\d)')


def clean_streets(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Normalize street_1/street_2 and extract embedded units; returns (df, counts)."""
    # email mistakenly in street_1: promote street_2 if it looks like an address, else null
    s1 = df['contributor_street_1'].astype('string')
    email_mask = s1.str.contains('@', na=False, regex=False)
    df['_street_email_in_s1'] = email_mask

    s2 = df['contributor_street_2'].astype('string')
    looks_addr = s2.str.contains(r'\d', na=False) | s2.str.contains(r'PO\s*BOX', case=False, na=False)
    swap_mask = email_mask & looks_addr
    null_mask = email_mask & ~swap_mask
    df['_street_swapped_from_s2'] = swap_mask
    df['_street_nulled_email'] = null_mask

    if swap_mask.any():
        df.loc[swap_mask, 'contributor_street_1'] = df.loc[swap_mask, 'contributor_street_2']
        df.loc[swap_mask, 'contributor_street_2'] = np.nan
    if null_mask.any():
        df.loc[null_mask, 'contributor_street_1'] = np.nan

    before = df['contributor_street_1'].copy()
    df['contributor_street_1'] = df['contributor_street_1'].apply(_normalize_street)
    n_normalized = int((before.fillna('') != df['contributor_street_1'].fillna('')).sum())

    df['contributor_street_2'] = df['contributor_street_2'].apply(_normalize_unit)

    # move units embedded in street_1 into empty street_2
    s1, s2, n_extracted = _extract_units(
        df['contributor_street_1'], df['contributor_street_2']
    )
    df['contributor_street_1'] = s1
    df['contributor_street_2'] = s2.apply(_normalize_unit)

    # strip unit words orphaned by the extraction
    df['contributor_street_1'] = df['contributor_street_1'].str.replace(
        _TRAILING_UNIT_RE, '', regex=True
    )

    # a street_1 that is only a floor ("3RD FLOOR") names no street: keep it as
    # the unit and leave street_1 empty for the donor-history recoveries
    n_normalized += _move_floor_only_street(df)

    # FEC sometimes has the donor's own name instead of the address: null it
    street = df['contributor_street_1'].fillna('')
    first_names = df['contributor_first_name'].fillna('').str.strip().str.upper()
    last_names = df['contributor_last_name'].fillna('').str.strip().str.upper()

    is_name = (
        ((street == first_names) & (first_names.str.len() >= 3))
        | ((street == last_names) & (last_names.str.len() >= 3))
        | (street == first_names + ' ' + last_names)
        | (street == last_names + ' ' + first_names)
    )
    if is_name.any():
        df.loc[is_name, 'contributor_street_1'] = np.nan
        n_normalized += int(is_name.sum())

    counts = {'streets_normalized': n_normalized, 'units_extracted': n_extracted}
    return df, counts


def _move_floor_only_street(df: pd.DataFrame) -> int:
    """street_1 that is only a floor designation -> street_2 when that is empty; street_1 becomes NULL so the recovery steps can refill it from the donor's other filings."""
    street1 = df['contributor_street_1']
    floor_only = street1.fillna('').astype(str).str.match(FLOOR_ONLY_RE)
    if not floor_only.any():
        return 0
    street2 = df['contributor_street_2']
    s2_blank = street2.isna() | (street2.fillna('').astype(str).str.strip() == '')
    move = floor_only & s2_blank
    if move.any():
        df['contributor_street_2'] = df['contributor_street_2'].astype(object)
        df.loc[move, 'contributor_street_2'] = street1[move].map(_normalize_unit)
        df.loc[move, 'contributor_street_1'] = np.nan
    # with a unit already in street_2 the floor stays put: recovery treats a
    # floor-only street_1 as non-usable and replaces it from the donor history
    return int(move.sum())


def _normalize_street(s: str) -> str:
    """Normalize a single street address string."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()

    # HTML entities, stray semicolons / brackets / colons
    s = re.sub(r'&SHY;', '', s)
    s = re.sub(r'&AMP;', '&', s)
    s = re.sub(r'&[A-Z]+;', '', s)
    s = s.replace(';', '')
    s = s.replace('[', '').replace(']', '')
    s = re.sub(r'\s*:', ' ', s)

    # an email is not an address
    if '@' in s:
        return np.nan

    # placeholder words, FEC committee ids, bare house numbers
    if s in _JUNK_STREETS or _INVALID_STREET_RE.match(s):
        return np.nan

    # PO box variants missing PO or BOX; digits required so street names
    # like BOX CANYON RD are untouched
    s = _SHORT_POBOX_RE.sub(r'PO BOX \1', s)

    # leading OCR digit/letter confusion (E to 3, I to 1)
    s = re.sub(r'^E(\d{2,})\b', lambda match: '3' + match.group(1), s)
    s = re.sub(r'^I(\d{3,})\b', lambda match: '1' + match.group(1), s)

    # unit/suite written before the address: move it to the end
    match = re.match(r'^((?:UNIT|APT|STE|SUITE)\s+\S+)\s+(\d+\s+.+)$', s)
    if match:
        s = match.group(2).strip() + ' ' + match.group(1).strip()

    s = _TRAILING_UNIT_RE.sub('', s)

    s = POBOX_RE.sub('PO BOX', s)
    s = re.sub(r'\s+', ' ', s)

    for pattern, replacement in STREET_TYPO_RULES:
        s = pattern.sub(replacement, s)

    # A direction stuck to the house number is not a house suffix.
    s = re.sub(r'^(\d+)([NSEW])\s+', r'\1 \2 ', s)

    # Missing space between house number and street name. A single other
    # letter followed by whitespace is part of the house number (14A, 704C);
    # a leading ordinal (3RD FLOOR, 21ST ST) is one word, never split.
    s = _split_fused_house_number(s)

    for rules in (DIR_PREFIX, DIR_MID, STREET_TYPES, DIR_SUFFIX):
        for pattern, replacement in rules:
            s = pattern.sub(replacement, s)

    s = s.replace(',', ' ').strip()
    s = re.sub(r'\s+', ' ', s)

    # period cleanup: specific patterns first, catch-all last
    s = re.sub(r'\.{2,}', '.', s)
    s = re.sub(r'\bP\.0\.\s*BOX\b', 'PO BOX', s)
    s = re.sub(r'(\d)\.([A-Z])', r'\1 \2', s)
    s = re.sub(r'\bSO\.(?=\s|$)', 'S', s)
    s = re.sub(r'(\d+(?:ST|ND|RD|TH))\.', r'\1', s)
    # compound direction dots must run before single-letter cleanup (S.W stays SW)
    s = re.sub(r'\b([NS])\.([EW])\b\.?', r'\1\2', s)
    s = re.sub(r'\b([NSEW])\.\s*([A-Z])', r'\1 \2', s)
    s = re.sub(r'\b([A-Z])\.\s', r'\1 ', s)
    s = re.sub(r'\b(UNIV|PT|NO|CTR|DEPT|BLDG|GEN|GOVT|NATL)\.\s*', r'\1 ', s)
    s = re.sub(r'([A-Z]{2,})\.\s', r'\1 ', s)
    # stray state code + ZIP fragment at end; the trailing digit is required
    # or this eats legitimate 2-letter street types ("FIGUEROA ST.")
    s = re.sub(r'\s+[A-Z]{2}\.\s*\d+$', '', s)

    s = s.rstrip('.')

    s = re.sub(r'\s+', ' ', s).strip()
    return s


_FUSED_HOUSE_NUMBER_RE = re.compile(r'^(\d+)([A-Z])(?=[A-Z])')
_LEADING_ORDINAL_RE = re.compile(r'^(\d+)(ST|ND|RD|TH)\b')


def _ordinal_suffix(number: str) -> str:
    """English ordinal suffix of a number: 1 -> ST, 3 -> RD, 12 -> TH, 23 -> RD."""
    value = int(number)
    if value % 100 in (11, 12, 13):
        return 'TH'
    return {1: 'ST', 2: 'ND', 3: 'RD'}.get(value % 10, 'TH')


def _split_fused_house_number(s: str) -> str:
    """'123MAIN ST' -> '123 MAIN ST', but a correct ordinal ('3RD FLOOR', '21ST ST') stays one word; '12ST JAMES PL' (12 takes TH) is still split."""
    match = _LEADING_ORDINAL_RE.match(s)
    if match and match.group(2) == _ordinal_suffix(match.group(1)):
        return s
    return _FUSED_HOUSE_NUMBER_RE.sub(r'\1 \2', s)


def _normalize_unit(s: str) -> str:
    """Normalize a unit/apt/suite string; placeholders ('NONE', '.', 'N/A', 'HOME') are no unit."""
    if pd.isna(s) or not str(s).strip():
        return np.nan

    s = str(s).strip().upper()
    if s.strip(' .,-') in _JUNK_UNITS or _has_no_unit_text(s):
        return np.nan
    for pattern, replacement in UNIT_RULES:
        s = pattern.sub(replacement, s)
    return s.strip()


def _strip_named_unit(streets: pd.Series) -> pd.Series:
    """Remove a trailing named unit and its leftover comma/whitespace."""
    stripped = streets.str.replace(UNIT_EXTRACT, '', regex=True).str.strip()
    return stripped.str.rstrip(',').str.strip()


def _extract_units(street1: pd.Series, street2: pd.Series) -> tuple[pd.Series, pd.Series, int]:
    """Move unit info embedded in street_1 into empty street_2; returns (s1, s2, n_extracted)."""
    s1 = street1.fillna('').astype(str).replace({'nan': ''})
    s2 = street2.fillna('').astype(str).replace({'nan': ''})
    s2_blank = s2.str.strip().eq('')
    n_extracted = 0

    # named units (APT, STE, UNIT, ...)
    named_unit = s1.str.extract(UNIT_EXTRACT)[0]
    move_named = named_unit.notna() & s2_blank
    if move_named.any():
        n_extracted += int(move_named.sum())
        s2 = s2.where(~move_named, named_unit.str.strip())
        s1 = s1.where(~move_named, _strip_named_unit(s1))
        s2_blank = s2.str.strip().eq('')

    # A second pass is intentional: after SUITE is removed, a preceding
    # "OFFICE PARK" can itself match the legacy unit rule.
    same_named = named_unit.notna() & ~s2_blank & (
        named_unit.str.strip().str.upper() == s2.str.strip().str.upper()
    )
    if same_named.any():
        s1 = s1.where(~same_named, _strip_named_unit(s1))

    # hash units (#5A, # 200)
    match_hash = s1.str.extract(HASH_EXTRACT)
    has_hash = match_hash[0].notna() & s2_blank
    if has_hash.any():
        n_extracted += int(has_hash.sum())
        s2 = s2.where(~has_hash, match_hash[0].str.strip())
        s1 = s1.where(
            ~has_hash,
            s1.str.replace(HASH_EXTRACT, '', regex=True).str.strip()
        )

    return s1.replace({'': np.nan}), s2.replace({'': np.nan}), n_extracted


# City cleaning

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


def _changed(before: pd.Series, after: pd.Series) -> int:
    """Rows whose value changed; a blank that stays blank is not a change."""
    return int((before.fillna('') != after.fillna('')).sum())


def _zip5(df: pd.DataFrame) -> pd.Series | None:
    """ZIP5 derived from the raw contributor_zip (clean_zips runs after the cities); None without that column."""
    if 'contributor_zip' not in df.columns:
        return None
    zip5, _n_invalid = _clean_zip_raw(df['contributor_zip'])
    return zip5


def clean_cities(df: pd.DataFrame, fuzzy: bool = True, report_dir: str | None = None) -> tuple[pd.DataFrame, dict]:
    """Clean city names; report_dir writes auto_city_fixes.json for review. Returns (df, counts)."""
    counts = {'known_fixes': 0, 'fuzzy_fixes': 0, 'punctuation_cleaned': 0}

    cities = df['contributor_city'].astype(str).str.strip().str.upper()

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
    counts['punctuation_cleaned'] = _changed(before, cities)

    # state codes stuck at end of city
    before = cities.copy()
    cities = cities.str.replace(STATE_IN_CITY, '', regex=True).str.strip()
    counts['punctuation_cleaned'] += _changed(before, cities)

    cities = cities.str.replace(r'\s{2,}', ' ', regex=True)

    before = cities.copy()
    cities = cities.replace(CITY_NORMALIZE)
    states = df['contributor_state'].fillna('').astype(str).str.strip().str.upper()
    state_fixes = pd.Series(
        list(zip(cities, states)), index=df.index
    ).map(CITY_STATE_NORMALIZE)
    cities = state_fixes.fillna(cities)
    zip5 = _zip5(df)
    if zip5 is not None:
        # short forms whose city depends on the ZIP ('NY' is NEW YORK only in Manhattan)
        zip3 = zip5.fillna('').astype(str).str[:3]
        zip_fixes = pd.Series(
            [CITY_ZIP3_NORMALIZE.get(key) for key in zip(cities, states, zip3)],
            index=df.index, dtype=object,
        )
        cities = zip_fixes.fillna(cities)
    counts['known_fixes'] = _changed(before, cities)
    table_fixed = before.fillna('') != cities.fillna('')
    df['contributor_city'] = cities

    if fuzzy:
        auto_fixes = _auto_detect_city_typos(df)
        if auto_fixes:
            # applied per (state, city, ZIP5): a fix learned in one state or at
            # one ZIP never renames the same spelling anywhere else
            fixed = pd.Series(
                [auto_fixes.get(key) for key in zip(states, df['contributor_city'], zip5)],
                index=df.index, dtype=object,
            )
            before = df['contributor_city'].copy()
            df['contributor_city'] = fixed.fillna(df['contributor_city'])
            counts['fuzzy_fixes'] = _changed(before, df['contributor_city'])
            table_fixed |= fixed.notna()

        if report_dir:
            report = [
                {'state': state, 'city': city, 'zip5': zip_code, 'fix': fix}
                for (state, city, zip_code), fix in sorted(auto_fixes.items())
            ]
            report_path = Path(report_dir) / 'auto_city_fixes.json'
            with open(report_path, 'w', encoding='utf-8') as handle:
                json.dump(report, handle, indent=2, ensure_ascii=False)

    df[CITY_TABLE_FIXED] = table_fixed

    # expand ST./MT./FT. abbreviations LAST so the fuzzy pass above still
    # matches its "ST."-form protected list
    df['contributor_city'] = df['contributor_city'].map(expand_city_abbreviations)

    return df, counts


def _swaps_place_qualifier(city: str, other: str) -> bool:
    """True when the two names differ only by whole qualifier words (EAST HARTFORD / WEST HARTFORD, WEST BLOOMFIELD TOWNSHIP / BLOOMFIELD TOWNSHIP)."""
    words, other_words = city.split(), other.split()
    only_city = [word for word in words if word not in other_words]
    only_other = [word for word in other_words if word not in words]
    differing = only_city + only_other
    return bool(differing) and all(word in _PLACE_QUALIFIERS for word in differing)


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


# ZIP cleaning

def clean_zips(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Extract and normalize 5-digit ZIP codes; returns (df, counts)."""
    # FEC sends 5- or 9-digit; keep the 5-digit form under the same column name
    df['contributor_zip'], n_invalid = _clean_zip_raw(df['contributor_zip'])
    return df, {
        'cleaned': int(df['contributor_zip'].notna().sum()),
        'invalid_nulled': n_invalid,
    }


def _clean_zip_raw(raw: pd.Series) -> tuple[pd.Series, int]:
    """Clean raw ZIP strings to 5 digits; returns (series, n_invalid_nulled)."""
    raw = raw.astype(str).str.strip().str.replace(r'[^\d]', '', regex=True)

    result = raw.str.zfill(5).str[:5].where(
        raw.str.len() <= 5, raw.str.zfill(9).str[:5]
    )

    # lowest USPS-assigned ZIP is 00501; below is unassigned (catches digit-drop
    # typos like "00034"). nulling is safe: the per-donor same-street fill
    # downstream restores the ZIP from the donor's own other filings.
    invalid = ~result.str.match(r'^\d{5}$', na=False) | (result < '00501')
    n_invalid = int(invalid.sum())
    result[invalid] = np.nan

    return result, n_invalid
