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

    # the ZIP box held the house number: take the typed city/ZIP out of the
    # street while the ZIP is still raw; clean_cities writes the place fields
    n_house_zip = _split_house_number_zip(df)

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

    counts = {
        'streets_normalized': n_normalized,
        'units_extracted': n_extracted,
        'house_number_zip': n_house_zip,
    }
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


# ZIP box holding the house number
#
# A few filings reach FEC with the house number in the ZIP box (ZIP = house
# number + '0001', or the bare 5-digit house number) and the real "<CITY> <ZIP>"
# typed at the end of street_1 ("12230 HOLLOW ROAD KAMAS 84036") or in street_2
# ("POTO 2085", cut by the field length). The city and state on those filings
# were derived from the fake ZIP (ALBANY NY for 12230), which pins the donor in
# another state. clean_streets takes the typed place out of the street and parks
# it in these working columns; clean_cities writes it to city/state/ZIP, so each
# change is recorded by the audit step that owns the field.
HOUSE_ZIP_CITY = '_house_zip_city'
HOUSE_ZIP_STATE = '_house_zip_state'
HOUSE_ZIP_ZIP = '_house_zip_zip'
_HOUSE_ZIP_COLUMNS = (HOUSE_ZIP_CITY, HOUSE_ZIP_STATE, HOUSE_ZIP_ZIP)

_LEADING_HOUSE_NUMBER_RE = re.compile(r'^(\d{3,5})\s')
# '<house> <street words> <city words> <ZIP5>[-ZIP4]'; the city/street split is
# decided by the city names filed at that ZIP
_STREET1_PLACE_RE = re.compile(r'^(\d{3,5} .*[A-Z].*?),? (\d{5})(?:-?\d{4})?$')
_STREET2_PLACE_RE = re.compile(r'^([A-Z]{3,}(?: [A-Z]+)*),? (\d{4,5})$')
_MAX_CITY_WORDS = 3
# a cut-off city ('POTO' + '2085') is completed only from a city/state/ZIP that
# this many different people file, so no single person's address is copied
_MIN_PLACE_FILERS = 3
# a typed street of only these words ('12230 RD') names no street to match
_STREET_WORDS_ONLY = frozenset(abbr for _pattern, abbr in STREET_TYPES) | {
    'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW',
}


def _upper_text(df: pd.DataFrame, column: str) -> pd.Series:
    """Column as stripped uppercase text ('' for blanks); an absent column is all ''."""
    if column not in df.columns:
        return pd.Series('', index=df.index, dtype=object)
    values = df[column].astype(object)
    return values.where(values.notna(), '').astype(str).str.strip().str.upper()


def _person_key(df: pd.DataFrame) -> pd.Series:
    return _upper_text(df, 'contributor_first_name') + '\x00' + _upper_text(df, 'contributor_last_name')


class _FiledPlaces:
    """City / state / ZIP5 of the other filings: which city names exist at a ZIP (anyone's filings) and the state and street one person files there (that person's own)."""

    def __init__(self, df: pd.DataFrame, exclude: pd.Series):
        zip5, _n_invalid = _clean_zip_raw(df['contributor_zip'])
        frame = pd.DataFrame({
            'city': _upper_text(df, 'contributor_city').str.rstrip('.,'),
            'state': _upper_text(df, 'contributor_state'),
            'zip5': zip5.fillna('').astype(str),
            'person': _person_key(df),
            'street': _upper_text(df, 'contributor_street_1'),
        })
        self.frame = frame[~exclude & (frame['city'] != '') & (frame['zip5'] != '')]
        self.city_zips = set(zip(self.frame['city'], self.frame['zip5']))

    def attested(self, city: str, zip5: str) -> bool:
        return (city, zip5) in self.city_zips

    def _own_rows(self, person: str, city: str, zip5: str) -> pd.DataFrame:
        frame = self.frame
        if person == '\x00':  # no name: nobody's own filings
            return frame.iloc[0:0]
        return frame[(frame['person'] == person) & (frame['city'] == city) & (frame['zip5'] == zip5)]

    def own_state(self, person: str, city: str, zip5: str) -> str:
        """The single state this person files the city and ZIP under; '' when none or several."""
        states = set(self._own_rows(person, city, zip5)['state']) - {''}
        return states.pop() if len(states) == 1 else ''

    def own_street(self, person: str, city: str, zip5: str, typed: str) -> str:
        """The person's own street at the city/ZIP with the same house number that ends with the typed street ('12230 BONE HOLLOW RD' for '12230 HOLLOW RD'); '' unless exactly one fits."""
        house, _space, rest = typed.partition(' ')
        if not set(rest.split()) - _STREET_WORDS_ONLY:
            return ''
        fits = {
            own for own in map(_normalize_street, set(self._own_rows(person, city, zip5)['street']))
            if isinstance(own, str) and own.startswith(house + ' ') and own.endswith(' ' + rest)
        }
        return fits.pop() if len(fits) == 1 else ''

    def complete(self, city_start: str, zip_start: str) -> tuple[str, str, str] | None:
        """(city, ZIP5, state) for a cut-off '<city> <ZIP>': one widely filed city/state must match both starts; of several ZIPs only an area ZIP (ZCTA) is kept, else the ZIP stays ''."""
        from fec.cleaning.pipeline.address_fixes.state_zip import is_zcta

        frame = self.frame[
            self.frame['city'].str.startswith(city_start)
            & self.frame['zip5'].str.startswith(zip_start)
            & (self.frame['state'] != '')
        ]
        filers = frame.groupby(['city', 'state', 'zip5'])['person'].nunique()
        matches = filers[filers >= _MIN_PLACE_FILERS].index
        places = {(city, state) for city, state, _zip in matches}
        if len(places) != 1:
            return None
        city, state = places.pop()
        zips = sorted({zip_code for _city, _state, zip_code in matches})
        if len(zips) > 1:
            zips = [zip_code for zip_code in zips if is_zcta(zip_code)]
        return city, (zips[0] if len(zips) == 1 else ''), state


def _street1_place(street: str, house: str, places: _FiledPlaces) -> tuple[str, str, str] | None:
    """(street, city, ZIP5) from '<street> <city> <ZIP>': the longest city other filings name at that ZIP, with a house number and a street word left before it."""
    match = _STREET1_PLACE_RE.match(street)
    if not match or match.group(2) == house.zfill(5):
        return None
    words, zip5 = match.group(1).replace(',', ' ').split(), match.group(2)
    for n_words in range(min(_MAX_CITY_WORDS, len(words) - 2), 0, -1):
        city, rest = ' '.join(words[-n_words:]), words[:-n_words]
        if places.attested(city, zip5) and any(re.search(r'[A-Z]', word) for word in rest[1:]):
            return ' '.join(rest), city, zip5
    return None


def _street2_place(street2: str, house: str, places: _FiledPlaces) -> tuple[str, str, str] | None:
    """(city, ZIP5, state) from '<city> <ZIP>' in street_2; a cut-off 'POTO 2085' is completed from the places many people file."""
    match = _STREET2_PLACE_RE.match(street2)
    if not match or match.group(2) == house.zfill(5):
        return None
    city, zip_text = match.groups()
    if len(zip_text) == 5 and places.attested(city, zip_text):
        return city, zip_text, ''
    return places.complete(city, zip_text)


def _split_house_number_zip(df: pd.DataFrame) -> int:
    """Rows whose ZIP box holds the house number and whose street holds the real city + ZIP: the place moves to the HOUSE_ZIP_* columns; returns rows fixed.

    The state comes from the person's own filings of that city and ZIP (same
    first and last name), else from the ZIP; other people's filings only show
    which city names exist at a ZIP. Rows with no readable city and state are
    left as filed."""
    if 'contributor_street_1' not in df.columns or 'contributor_zip' not in df.columns:
        return 0
    street1 = _upper_text(df, 'contributor_street_1')
    digits = _upper_text(df, 'contributor_zip').str.replace(r'\D', '', regex=True)
    house = street1.str.extract(_LEADING_HOUSE_NUMBER_RE)[0].fillna('')
    candidate = (house != '') & (
        (digits == house + '0001') | ((digits == house) & (house.str.len() == 5))
    )
    if not candidate.any():
        return 0

    from fec.cleaning.pipeline.address_fixes.state_zip import zip_state

    street2 = _upper_text(df, 'contributor_street_2')
    person = _person_key(df)
    places = _FiledPlaces(df, candidate)
    fixes = {}
    for index in df.index[candidate]:
        found = _street1_place(street1[index], house[index], places)
        if found:
            new_street1, city, zip5 = found
            place_state, clear_street2 = '', False
        else:
            found = _street2_place(street2[index], house[index], places)
            if not found:
                continue
            city, zip5, place_state = found
            new_street1, clear_street2 = street1[index], True
        state = places.own_state(person[index], city, zip5) or place_state or zip_state(zip5)
        if not state:
            continue
        # the street typed twice and cut by the field ('11425 TWINING LN 11425
        # TWINING L'); a word the filing dropped ('12230 HOLLOW ROAD') comes back
        # from the person's own filings of the same house at that city and ZIP
        new_street1 = _drop_repeated_street(_normalize_street(new_street1))
        new_street1 = places.own_street(person[index], city, zip5, new_street1) or new_street1
        fixes[index] = (new_street1, clear_street2, city, state, zip5)

    for column in _HOUSE_ZIP_COLUMNS:
        df[column] = pd.Series(None, index=df.index, dtype=object)
    for index, (new_street1, clear_street2, city, state, zip5) in fixes.items():
        df.at[index, 'contributor_street_1'] = new_street1
        if clear_street2:
            df.at[index, 'contributor_street_2'] = np.nan
        df.at[index, HOUSE_ZIP_CITY] = city
        df.at[index, HOUSE_ZIP_STATE] = state
        df.at[index, HOUSE_ZIP_ZIP] = zip5
    return len(fixes)


def _apply_house_number_zip(df: pd.DataFrame) -> int:
    """Write the place clean_streets read from the street into city/state/ZIP (a ZIP left '' is blanked) and drop the working columns; returns rows changed."""
    if HOUSE_ZIP_CITY not in df.columns:
        return 0
    fixed = df[HOUSE_ZIP_CITY].notna()
    for column, source in (
        ('contributor_city', HOUSE_ZIP_CITY),
        ('contributor_state', HOUSE_ZIP_STATE),
        ('contributor_zip', HOUSE_ZIP_ZIP),
    ):
        if fixed.any() and column in df.columns:
            df.loc[fixed, column] = df.loc[fixed, source].replace('', np.nan)
    df.drop(columns=list(_HOUSE_ZIP_COLUMNS), inplace=True)
    return int(fixed.sum())


_HOUSE_TOKEN_RE = re.compile(r'^\d+[A-Z]?$')
_DIRECTION_TOKENS = frozenset({'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW'})


def _drop_repeated_street(s):
    """'11425 TWINING LN 11425 TWINING L' -> '11425 TWINING LN': the filer typed the street twice and the 34-character FEC field cut the copy.

    Only a copy that starts with the same house number and is a prefix of the
    street before it is dropped ('396 FOREST AVE 396 FOREST AVE' too); a grid
    address such as '1300 E 1300 S' is not a copy and stays."""
    if not isinstance(s, str):
        return s
    tokens = s.split()
    if len(tokens) < 4 or not _HOUSE_TOKEN_RE.match(tokens[0]):
        return s
    for index in range(2, len(tokens) - 1):
        if tokens[index] != tokens[0]:
            continue
        # a separator between the two copies ('1020 HULL ST / 1020 HULL ST') goes too
        head = tokens[:index]
        while head and not any(char.isalnum() for char in head[-1]):
            head.pop()
        first, copy = ' '.join(head), ' '.join(tokens[index:])
        # what is kept must still name a street ('159 W 159 WEST' is not '159 W')
        names_street = any(
            token not in _DIRECTION_TOKENS and re.search(r'[A-Z]', token) for token in head[1:]
        )
        if names_street and first.startswith(copy):
            return first
    return s


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
    for index in range(2, len(tokens) - 1):
        if tokens[index] != tokens[0]:
            continue
        # a separator between the two copies ('1020 HULL ST / 1020 HULL ST') goes too
        head = tokens[:index]
        while head and not any(char.isalnum() for char in head[-1]):
            head.pop()
        first, copy = ' '.join(head), ' '.join(tokens[index:])
        # what is kept must still name a street ('159 W 159 WEST' is not '159 W')
        names_street = any(
            token not in _DIRECTION_TOKENS and re.search(r'[A-Z]', token) for token in head[1:]
        )
        if names_street and first.startswith(copy):
            return first
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

    # city/state/ZIP that clean_streets read from a street whose ZIP box held
    # the house number (counted there); written first so the tables below see
    # the real place
    _apply_house_number_zip(df)

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
