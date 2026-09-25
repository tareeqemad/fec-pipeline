"""US places: state names and boxes, distances, and which Nominatim result is the filed town itself.

A town-level pin must be the town. The old free-text query 'CITY, ST ZIP, USA'
returned whatever matched first, and for a PO-box ZIP that OSM does not know that
was a POI named '... USA' (Lego Miniland USA in Carlsbad for San Francisco 94141,
Murphy USA in Sealy for Austin 78711), a county (Jackson County MS for Jackson
39207), a road or building named like the town (a 'Crown Point' road in
Zionsville) or the neighbouring city (North Little Rock for Little Rock 72217).
choose_town accepts only a settlement whose name is the filed city's and that
lies in the filed state.
"""

import math
import re
import unicodedata

from fec.config.geography import US_STATE_BBOX

# Nominatim's structured 'state' needs the full name: state='LA' was read as Los Angeles.
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

# Nominatim addresstypes of a populated place. A county, road, building, amenity,
# shop, landuse or railway station named like the town is not the town.
# 'locality' covers census-designated places (Jefferson LA is boundary=census).
SETTLEMENT_TYPES = frozenset({
    "city", "town", "village", "hamlet", "municipality", "borough",
    "suburb", "city_district", "quarter", "neighbourhood", "locality",
})

# same-name towns farther apart than this from the filed ZIP's area are different places
SAME_TOWN_KM = 50
# a NY/WI civil town surrounds the city or village of its name (Town of Ithaca's point
# is 4 km from the City of Ithaca, where the post office is)
SURROUNDING_TOWN_KM = 25
# the names that identify a place; other translations do not (Kaneohe's Malagasy
# name is 'Honolulu'). _place_name is the name of the place node a boundary carries.
_NAME_KEYS = frozenset({
    "name", "name:en", "alt_name", "alt_name:en", "short_name", "short_name:en",
    "official_name", "official_name:en", "loc_name", "_place_name", "_place_name:en",
})

_WORD_FORMS = {
    "SAINT": "ST", "SAINTE": "STE", "FORT": "FT", "MOUNT": "MT",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}
# a leading place-type marker to strip: "CITY OF ", "TOWN OF ", "THE "
_TOWN_PREFIX_RE = re.compile(r"^(?:THE |(?:CITY|TOWN|VILLAGE|BOROUGH|TOWNSHIP) OF )")
# a trailing "TOWNSHIP"/"TWP" to strip: "OXFORD TOWNSHIP"
_TOWN_SUFFIX_RE = re.compile(r" (?:TOWNSHIP|TWP)$")
# any township marker, leading or trailing: "TOWN OF X", "X TOWNSHIP"
_TOWNSHIP_RE = re.compile(r"^TOWN(?:SHIP)? OF | (?:TOWNSHIP|TWP)$")


# great-circle distance between two lat/lng points, in km
def distance_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, first)
    lat2, lng2 = map(math.radians, second)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    value = math.sin(dlat / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(value))


# true if coords fall in any US state/territory bounding box
def in_us_bounds(lat: float, lng: float) -> bool:
    """True if coords fall in any US state/territory bbox (+1 deg margin) -- the global US guard."""
    for lat_min, lat_max, lng_min, lng_max in US_STATE_BBOX.values():
        if lat_min - 1 <= lat <= lat_max + 1 and lng_min - 1 <= lng <= lng_max + 1:
            return True
    return False


# true if coords are plausible for the given US state
def valid_for_state(lat: float, lng: float, state: str) -> bool:
    """True if coords are plausible for the given US state (1 deg border margin)."""
    if not state or state not in US_STATE_BBOX:
        return in_us_bounds(lat, lng)  # unknown state: accept only if inside the US
    lat_min, lat_max, lng_min, lng_max = US_STATE_BBOX[state]
    return (lat_min - 1 <= lat <= lat_max + 1) and (lng_min - 1 <= lng <= lng_max + 1)


# normalized comparable form of a town name
def town_name_key(name: str) -> str:
    """Comparable form of a town name: 'Saint Louis' / 'ST. LOUIS', 'McLean' / 'MC LEAN', 'City of Boulder' / 'BOULDER', 'The Bronx' / 'BRONX' agree."""
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    text = re.sub("['.\N{RIGHT SINGLE QUOTATION MARK}]", "", text)  # apostrophes and periods join: D'ALENE, ST.
    text = re.sub(r"[^A-Z0-9]+", " ", text).strip()
    text = _TOWN_SUFFIX_RE.sub("", _TOWN_PREFIX_RE.sub("", text))
    return "".join(_WORD_FORMS.get(word, word) for word in text.split())


# every name Nominatim gives for a result
def result_names(result: dict) -> set[str]:
    """The names Nominatim gives for a result: its name and English, alternative, short and official names."""
    names = {result.get("name") or ""}
    for key, value in (result.get("namedetails") or {}).items():
        if key in _NAME_KEYS and value:
            names.update(part.strip() for part in value.split(";"))
    return {name for name in names if name}


# true if the result is named like the filed city
def same_town_name(result: dict, city: str) -> bool:
    """The result is named like the filed city (North Little Rock is not Little Rock)."""
    wanted = town_name_key(city)
    return bool(wanted) and any(town_name_key(name) == wanted for name in result_names(result))


# true if a result is a civil town or township
def is_township(result: dict) -> bool:
    """A civil town or township by its own name ('Town of Ithaca', 'West Bloomfield Township')."""
    name = re.sub(r"[^A-Z0-9]+", " ", str(result.get("name") or "").upper()).strip()
    return bool(_TOWNSHIP_RE.search(name))


# true if the result's address type is a populated place
def is_settlement(result: dict) -> bool:
    return (result.get("addresstype") or "") in SETTLEMENT_TYPES


# a result's (lat, lng) as floats
def result_point(result: dict) -> tuple[float, float]:
    return float(result["lat"]), float(result["lon"])


# true if the result lies in the filed state
def in_state(result: dict, state: str) -> bool:
    """The result lies in the filed state: its ISO 3166-2 code when Nominatim gives one, and the state box.

    A structured search does not keep to its state: Houston, Missouri came back for Texas."""
    code = (result.get("address") or {}).get("ISO3166-2-lvl4") or ""
    if state in STATE_NAMES and code and code.upper() != f"US-{state}":
        return False
    lat, lng = result_point(result)
    return valid_for_state(lat, lng, state)


# results that are the filed town: settlement, name, state match
def matching_towns(results: list[dict], city: str, state: str) -> list[dict]:
    """The results that are the filed town: a settlement, named like the city, in the state (Nominatim's order kept)."""
    return [result for result in results or []
            if is_settlement(result) and same_town_name(result, city) and in_state(result, state)]


# pick the filed town among results, nearest to hint point
def choose_town(results: list[dict], city: str, state: str,
                near: tuple[float, float] | None = None) -> dict | None:
    """The filed town among Nominatim results, or None.

    Several places in one state can share the name (MAGNOLIA TX 62 km apart,
    ENTERPRISE AL, POTOMAC MD in Montgomery and in Allegany County). With near (the
    filed ZIP's area) the first town within SAME_TOWN_KM of it wins, else the town
    closest to it; without near, Nominatim's first (most important) town. A civil
    town yields to the city or village of its name inside it (Town / City of Ithaca).
    """
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
        # the city or village of that name inside the civil town is the postal town
        inner = [other for other in towns if not is_township(other)
                 and distance_km(result_point(other), result_point(town)) <= SURROUNDING_TOWN_KM]
        if inner:
            return inner[0]
    return town
