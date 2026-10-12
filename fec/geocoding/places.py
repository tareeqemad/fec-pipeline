"""US places, distances, and town matching."""

import math
import re
import unicodedata

from fec.config.geography import US_STATE_BBOX

# full state names; 'LA' means Los Angeles
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "United States Virgin Islands", "GU": "Guam",
    "AS": "American Samoa", "MP": "Northern Mariana Islands",
}

# Nominatim addresstypes that are populated places
SETTLEMENT_TYPES = frozenset({
    "city", "town", "village", "hamlet", "municipality", "borough",
    "suburb", "city_district", "quarter", "neighbourhood", "locality",
})

# same-name towns farther from ZIP area differ
SAME_TOWN_KM = 50
# civil town surrounds its namesake city
SURROUNDING_TOWN_KM = 25
# name keys that identify a place
_NAME_KEYS = frozenset({
    "name", "name:en", "alt_name", "alt_name:en", "short_name", "short_name:en",
    "official_name", "official_name:en", "loc_name", "_place_name", "_place_name:en",
})

_WORD_FORMS = {
    "SAINT": "ST", "SAINTE": "STE", "FORT": "FT", "MOUNT": "MT",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}
# a leading place-type marker like CITY OF
_TOWN_PREFIX_RE = re.compile(r"^(?:THE |(?:CITY|TOWN|VILLAGE|BOROUGH|TOWNSHIP) OF )")
# a trailing "TOWNSHIP"/"TWP" to strip: "OXFORD TOWNSHIP"
_TOWN_SUFFIX_RE = re.compile(r" (?:TOWNSHIP|TWP)$")
# any township marker, leading or trailing
_TOWNSHIP_RE = re.compile(r"^TOWN(?:SHIP)? OF | (?:TOWNSHIP|TWP)$")


EARTH_RADIUS_KM = 6371
EARTH_RADIUS_MILES = 3958.8


# great-circle distance in the radius's unit
def great_circle(first: tuple[float, float], second: tuple[float, float], radius: float) -> float:
    lat1, lng1 = map(math.radians, first)
    lat2, lng2 = map(math.radians, second)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    # rounding can push the root past 1
    return radius * 2 * math.asin(min(1.0, math.sqrt(value)))


# great-circle distance between two points in km
def distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    return great_circle(first, second, EARTH_RADIUS_KM)


# true if inside any US state box
def in_us_bounds(lat: float, lng: float) -> bool:
    for lat_min, lat_max, lng_min, lng_max in US_STATE_BBOX.values():
        if lat_min - 1 <= lat <= lat_max + 1 and lng_min - 1 <= lng <= lng_max + 1:
            return True
    return False


# true if coords are plausible for state
def valid_for_state(lat: float, lng: float, state: str) -> bool:
    if not state or state not in US_STATE_BBOX:
        return in_us_bounds(lat, lng)  # unknown state: accept anywhere in US
    lat_min, lat_max, lng_min, lng_max = US_STATE_BBOX[state]
    return (lat_min - 1 <= lat <= lat_max + 1) and (lng_min - 1 <= lng <= lng_max + 1)


# normalized comparable form of a town name
def town_name_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    text = re.sub("['.\N{RIGHT SINGLE QUOTATION MARK}]", "", text)  # apostrophes and periods join: D'ALENE, ST.
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    text = _TOWN_SUFFIX_RE.sub("", _TOWN_PREFIX_RE.sub("", text))
    return "".join(_WORD_FORMS.get(word, word) for word in text.split())


# every name Nominatim gives for a result
def result_names(result: dict) -> set[str]:
    names = {result.get("name") or ""}
    for key, value in (result.get("namedetails") or {}).items():
        if key in _NAME_KEYS and value:
            names.update(part.strip() for part in value.split(";"))
    return {name for name in names if name}


# true if result's name matches the city
def same_town_name(result: dict, city: str) -> bool:
    wanted = town_name_key(city)
    return bool(wanted) and any(town_name_key(name) == wanted for name in result_names(result))


# true for a civil town or township
def is_township(result: dict) -> bool:
    name = re.sub(r"[^A-Z0-9]+", " ", str(result.get("name") or "").upper()).strip()
    return bool(_TOWNSHIP_RE.search(name))


# true if result is a populated place
def is_settlement(result: dict) -> bool:
    return (result.get("addresstype") or "") in SETTLEMENT_TYPES


# a result's (lat, lng) as floats
def result_point(result: dict) -> tuple[float, float]:
    return float(result["lat"]), float(result["lon"])


# true if result lies in filed state
def in_state(result: dict, state: str) -> bool:
    code = (result.get("address") or {}).get("ISO3166-2-lvl4") or ""
    if state in STATE_NAMES and code and code.upper() != f"US-{state}":
        return False
    lat, lng = result_point(result)
    return valid_for_state(lat, lng, state)


# settlement results named like city, in state
def matching_towns(results: list[dict], city: str, state: str) -> list[dict]:
    return [result for result in results or []
            if is_settlement(result) and same_town_name(result, city) and in_state(result, state)]


# pick the filed town nearest the hint
def choose_town(results: list[dict], city: str, state: str,
                near: tuple[float, float] | None = None) -> dict | None:
    towns = matching_towns(results, city, state)
    if not towns:
        return None
    town = towns[0]
    if near is not None:
        close = [candidate for candidate in towns
                 if distance_km(result_point(candidate), near) <= SAME_TOWN_KM]
        town = close[0] if close else min(
            towns, key=lambda candidate: distance_km(result_point(candidate), near))
    if is_township(town):
        # inner namesake village is the postal town
        inner = [other for other in towns if not is_township(other)
                 and distance_km(result_point(other), result_point(town)) <= SURROUNDING_TOWN_KM]
        if inner:
            return inner[0]
    return town
