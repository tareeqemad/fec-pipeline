"""Fix ZIP fields holding the house number."""
import re

import numpy as np
import pandas as pd

from fec.cleaning.addresses.fixes.state_zip import is_zcta, zip_state
from fec.cleaning.addresses.street_text import (
    drop_repeated_street,
    normalize_street,
)
from fec.cleaning.addresses.zips import clean_zip_raw
from fec.config.streets import (
    STREET_TYPES,
)

# working columns for the street-typed place
HOUSE_ZIP_CITY = '_house_zip_city'


HOUSE_ZIP_STATE = '_house_zip_state'


HOUSE_ZIP_ZIP = '_house_zip_zip'


_HOUSE_ZIP_COLUMNS = (HOUSE_ZIP_CITY, HOUSE_ZIP_STATE, HOUSE_ZIP_ZIP)


# a leading 3-5 digit house number
_LEADING_HOUSE_NUMBER_RE = re.compile(r'^(\d{3,5})\s')


# house, street, city words, then ZIP5
_STREET1_PLACE_RE = re.compile(r'^(\d{3,5} .*[A-Z].*?),? (\d{5})(?:-?\d{4})?$')


# city then a ZIP fragment, in street_2
_STREET2_PLACE_RE = re.compile(r'^([A-Z]{3,}(?: [A-Z]+)*),? (\d{4,5})$')


_MAX_CITY_WORDS = 3


# so no one person's address is copied
_MIN_PLACE_FILERS = 3


# these words alone name no street
_STREET_WORDS_ONLY = frozenset(abbr for _pattern, abbr in STREET_TYPES) | {
    'N', 'S', 'E', 'W', 'NE', 'NW', 'SE', 'SW',
}


# uppercase column text; missing column is blank
def _upper_text(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series('', index=df.index, dtype=object)
    values = df[column].astype(object)
    return values.where(values.notna(), '').astype(str).str.strip().str.upper()


# donor key from first and last name
def _person_key(df: pd.DataFrame) -> pd.Series:
    return _upper_text(df, 'contributor_first_name') + '\x00' + _upper_text(df, 'contributor_last_name')


class _FiledPlaces:
    """City, state and ZIP of other filings."""

    # index other filings, skipping rows being fixed
    def __init__(self, df: pd.DataFrame, exclude: pd.Series):
        zip5, _n_invalid = clean_zip_raw(df['contributor_zip'])
        frame = pd.DataFrame({
            'city': _upper_text(df, 'contributor_city').str.rstrip('.,'),
            'state': _upper_text(df, 'contributor_state'),
            'zip5': zip5.fillna('').astype(str),
            'person': _person_key(df),
            'street': _upper_text(df, 'contributor_street_1'),
        })
        self.frame = frame[~exclude & (frame['city'] != '') & (frame['zip5'] != '')]
        self.city_zips = set(zip(self.frame['city'], self.frame['zip5']))

    # true if city is filed at ZIP
    def attested(self, city: str, zip5: str) -> bool:
        return (city, zip5) in self.city_zips

    # person's own rows at city and ZIP
    def _own_rows(self, person: str, city: str, zip5: str) -> pd.DataFrame:
        frame = self.frame
        if person == '\x00':  # no name: nobody's own filings
            return frame.iloc[0:0]
        return frame[(frame['person'] == person) & (frame['city'] == city) & (frame['zip5'] == zip5)]

    # person's single state at city/ZIP, else blank
    def own_state(self, person: str, city: str, zip5: str) -> str:
        states = set(self._own_rows(person, city, zip5)['state']) - {''}
        return states.pop() if len(states) == 1 else ''

    # recover dropped street word from person's filings
    def own_street(self, person: str, city: str, zip5: str, typed: str) -> str:
        house, _space, rest = typed.partition(' ')
        if not set(rest.split()) - _STREET_WORDS_ONLY:
            return ''
        fits = {
            own for own in map(normalize_street, set(self._own_rows(person, city, zip5)['street']))
            if isinstance(own, str) and own.startswith(house + ' ') and own.endswith(' ' + rest)
        }
        return fits.pop() if len(fits) == 1 else ''

    # complete cut-off city/ZIP from widely filed places
    def complete(self, city_start: str, zip_start: str) -> tuple[str, str, str] | None:
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


# pull trailing city/ZIP out of street_1
def _street1_place(street: str, house: str, places: _FiledPlaces) -> tuple[str, str, str] | None:
    match = _STREET1_PLACE_RE.match(street)
    if not match or match.group(2) == house.zfill(5):
        return None
    words, zip5 = match.group(1).replace(',', ' ').split(), match.group(2)
    for n_words in range(min(_MAX_CITY_WORDS, len(words) - 2), 0, -1):
        city, rest = ' '.join(words[-n_words:]), words[:-n_words]
        if places.attested(city, zip5) and any(re.search(r'[A-Z]', word) for word in rest[1:]):
            return ' '.join(rest), city, zip5
    return None


# pull city/ZIP from street_2, completing cut-offs
def _street2_place(street2: str, house: str, places: _FiledPlaces) -> tuple[str, str, str] | None:
    match = _STREET2_PLACE_RE.match(street2)
    if not match or match.group(2) == house.zfill(5):
        return None
    city, zip_text = match.groups()
    if len(zip_text) == 5 and places.attested(city, zip_text):
        return city, zip_text, ''
    return places.complete(city, zip_text)


# fix rows with house number in ZIP
def split_house_number_zip(df: pd.DataFrame) -> int:
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
        # street may be typed twice or cut
        new_street1 = drop_repeated_street(normalize_street(new_street1))
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


# write recovered place to city/state/ZIP, drop columns
def apply_house_number_zip(df: pd.DataFrame) -> int:
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
