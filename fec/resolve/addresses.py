"""Normalize employer addresses and attach accepted cached coordinates."""

import re

import pandas as pd

from fec.cleaning.addresses.cities import clean_cities
from fec.cleaning.addresses.street_text import (
    _extract_units,
    _normalize_unit,
    normalize_street,
    numbered_street,
)
from fec.config.streets import HASH_EXTRACT, UNIT_EXTRACT, usps_unit_designators
from fec.env import CACHE_DIR
from fec.geocoding.validation import REVIEWED_POINTS, accepted_coordinates, is_foreign_address
from fec.resolve.cache import _read_json
from fec.resolve.locations import ADDRESS_FIELDS


_EDITORIAL_NOTE_RE = re.compile(r"\s*\([^()]*\)")
_ZIP_PLUS_FOUR_RE = re.compile(r"^(\d{5})-?\d{4}$")
_UNIT_CODE_RE = re.compile(r"[A-Z]|(?=[A-Z0-9\-/.]*\d)[A-Z0-9][A-Z0-9\-/.]*", re.IGNORECASE)
_TRAILING_UNIT_WORD_RE = re.compile(r"\s+(?:APT|UNIT|STE|SUITE)\s*$")


def _level_rank(level: str) -> int:
    """Prefer verified manual corrections."""
    return 4 if level in {"manual_census", "manual_verified"} else 3


def _address_key(values) -> tuple[str, ...]:
    street, *rest = (str(values.get(field) or "").strip().upper() for field in ADDRESS_FIELDS)
    return (numbered_street(street), *rest)


def _geocodes() -> dict[tuple[str, ...], tuple[float, float, str]]:
    """Read accepted cached points, preferring manual corrections."""
    cache = _read_json(CACHE_DIR / "geocode_cache.json")
    # a hand-checked point counts even when its key has no cache entry
    keys = {**{key: None for key in REVIEWED_POINTS}, **cache}
    coordinates = {}
    for key, result in keys.items():
        parts = key.split("|")
        if len(parts) != 4:
            continue
        latitude, longitude, level = accepted_coordinates(key, result)
        if latitude is None:
            continue
        address_key = _address_key(dict(zip(ADDRESS_FIELDS, parts)))
        known = coordinates.get(address_key)
        if known is None or _level_rank(level) > _level_rank(known[2]):
            coordinates[address_key] = (latitude, longitude, level)
    return coordinates


def normalize_us_streets(streets: pd.Series) -> pd.Series:
    """Normalize street text while keeping building, floor and suite details."""
    text = streets.fillna("").astype(str).str.replace(_EDITORIAL_NOTE_RE, " ", regex=True)
    text = text.str.split().str.join(" ")
    normalized = text.map(normalize_street)
    # a text the street normaliser reads as junk (a bare number) is kept, uppercased
    keep = normalized.notna() & normalized.astype(str).ne("")
    streets = normalized.where(keep, text.str.upper()).astype(str).str.strip()
    return streets.map(_abbreviate_trailing_unit).map(usps_unit_designators)


def _abbreviate_trailing_unit(street: str) -> str:
    """Normalize a trailing unit code without changing the street or building name."""
    match = UNIT_EXTRACT.search(street)
    if not match:
        return street
    unit_text = match.group(1).strip()
    if not _UNIT_CODE_RE.fullmatch(unit_text.split()[-1]):
        return street
    unit = _normalize_unit(unit_text)
    if not isinstance(unit, str) or not unit:
        return street
    return f"{street[:match.start()].rstrip(' ,')} {unit}".strip()


def _bare_street(street: str) -> str:
    """Remove trailing units to find the building address."""
    street = str(street or "").strip()
    while True:
        match = UNIT_EXTRACT.search(street) or HASH_EXTRACT.search(street)
        if not match:
            return street
        unit_text = match.group(1).strip()
        if not unit_text.startswith("#") and not _UNIT_CODE_RE.fullmatch(unit_text.split()[-1]):
            return street
        rest = street[:match.start()].rstrip(" ,-")
        if not rest:
            return street
        street = rest


def _donor_form_streets(streets: pd.Series) -> pd.Series:
    """Match the street form produced by donor address cleaning."""
    street_1, _street_2, _leftovers = _extract_units(streets, pd.Series("", index=streets.index))
    street_1 = street_1.fillna("").astype(str).str.replace(_TRAILING_UNIT_WORD_RE, "", regex=True)
    return street_1.str.strip()


def normalize_us_cities(cities: pd.Series, states: pd.Series) -> pd.Series:
    """Use the same city names as donor address cleaning."""
    frame = pd.DataFrame({
        "contributor_city": cities.fillna("").astype(str).str.strip(),
        "contributor_state": states.fillna("").astype(str).str.strip().str.upper(),
    })
    cleaned, _counts = clean_cities(frame)
    return cleaned["contributor_city"].fillna("").astype(str)


def _foreign_mask(frame: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [is_foreign_address(state, zipcode)
         for state, zipcode in zip(frame["employer_state"], frame["employer_zip"])],
        index=frame.index, dtype=bool,
    )


def normalize_location_addresses(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize US addresses; preserve foreign address text."""
    frame = frame.copy()
    for field in ADDRESS_FIELDS:
        frame[field] = frame[field].fillna("").astype(str).str.strip()
    foreign = _foreign_mask(frame)
    has_place = frame[list(ADDRESS_FIELDS)].ne("").any(axis=1)
    us = ~foreign & has_place
    if not us.any():
        return frame
    rows = frame.loc[us]
    frame.loc[us, "employer_address"] = normalize_us_streets(rows["employer_address"])
    frame.loc[us, "employer_city"] = normalize_us_cities(rows["employer_city"], rows["employer_state"])
    frame.loc[us, "employer_state"] = rows["employer_state"].str.upper()
    frame.loc[us, "employer_zip"] = rows["employer_zip"].str.replace(_ZIP_PLUS_FOUR_RE, r"\1", regex=True)
    return frame


def _attach_coordinates(frame: pd.DataFrame) -> pd.DataFrame:
    """Use accepted cached points, preferring manual corrections then the original text."""
    frame = frame.copy()
    if frame.empty:
        return frame
    geocodes = _geocodes()
    streets = frame["employer_address"].fillna("").astype(str)
    foreign = _foreign_mask(frame)
    donor_form = _donor_form_streets(streets).where(~foreign, streets)
    bare = streets.map(_bare_street).where(~foreign, streets)

    best: dict[object, tuple] = {}
    for index, row in frame.iterrows():
        normalized = _address_key(row)
        place = normalized[1:]
        raw_key = row["_raw_key"] if isinstance(row["_raw_key"], tuple) else normalized
        keys = [raw_key, normalized,
                (numbered_street(donor_form[index]), *place),
                (numbered_street(bare[index]), *place)]
        choice = None
        for order, key in enumerate(keys):
            point = geocodes.get(tuple(key))
            if point is None:
                continue
            rank = (_level_rank(point[2]), -order)
            if choice is None or rank > choice[0]:
                choice = (rank, point)
        best[index] = (normalized, choice)

    by_address: dict[tuple, tuple] = {}
    for normalized, choice in best.values():
        if choice is None:
            continue
        known = by_address.get(normalized)
        if known is None or choice[0][0] > known[0][0]:
            by_address[normalized] = choice
    for index, (normalized, choice) in best.items():
        shared = by_address.get(normalized)
        if shared is not None and (choice is None or shared[0][0] > choice[0][0]):
            choice = shared
        latitude, longitude = (choice[1][0], choice[1][1]) if choice else (None, None)
        frame.at[index, "employer_latitude"] = latitude
        frame.at[index, "employer_longitude"] = longitude
    return frame
