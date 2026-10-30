"""Accept address coordinates and keep the existing reviewed corrections."""

import math
import re
from urllib.parse import urlsplit

from fec.cleaning.addresses.street_text import numbered_street
from fec.config.geography import US_STATES, US_STATE_BBOX
from fec.config.tables import read_rows
from fec.env import RULES_DIR

EARTH_RADIUS_MILES = 3958.8
STREET_LEVEL_SOURCES = {"nominatim", "census", "google", "manual_census", "manual_verified", "nominatim_intl"}
US_TERRITORIES = {"AS", "GU", "MP", "PR", "VI"}
REVIEWED_WRONG_KM = 0.5


def is_po_box(street: str) -> bool:
    return bool(re.match(r"^P[.\s]*O[.\s]*BOX\b", street.strip(), re.IGNORECASE))


def unclear_house_number(street: str) -> bool:
    return bool(re.match(r"^[^A-Z0-9\s]+\d", street.strip(), re.IGNORECASE))


def is_foreign_address(state: str, zipcode: str) -> bool:
    state, zipcode = str(state or "").strip().upper(), str(zipcode or "").strip().upper()
    if state:
        return state not in US_STATES or bool(re.fullmatch(
            r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}", zipcode))
    if zipcode.endswith(".0"):
        zipcode = zipcode[:-2].zfill(5)
    return not bool(re.fullmatch(r"\d{5}(?:-?\d{4})?", zipcode))


def great_circle(first: tuple, second: tuple, radius: float) -> float:
    lat1, lng1 = map(math.radians, first)
    lat2, lng2 = map(math.radians, second)
    value = math.sin((lat2 - lat1) / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(value)))


def distance_km(first: tuple, second: tuple) -> float:
    return great_circle(first, second, 6371)


def valid_for_state(lat: float, lng: float, state: str) -> bool:
    state = state.strip().upper()
    bounds = [US_STATE_BBOX[state]] if state in US_STATE_BBOX else US_STATE_BBOX.values()
    return any(south - 1 <= lat <= north + 1 and west - 1 <= lng <= east + 1
               for south, north, west, east in bounds)


def _review_key(key: str) -> str:
    parts = [part.strip().upper() for part in key.split("|")]
    parts[0] = numbered_street(parts[0])
    return "|".join(parts)


def reviewed_point(key: str) -> tuple | None:
    point = REVIEWED_POINTS.get(_review_key(key))
    return tuple(point[:2]) if point else None


def is_reviewed_wrong(key: str, lat: float, lng: float) -> bool:
    wrong = REVIEWED_WRONG_POINTS.get(_review_key(key))
    return wrong is not None and distance_km((lat, lng), wrong[:2]) <= REVIEWED_WRONG_KM


def accepted_coordinates(key: str, entry: dict | None) -> tuple:
    reviewed = reviewed_point(key)
    if reviewed is not None:
        return *reviewed, "google"
    if not entry or entry.get("lat") is None or entry.get("lng") is None:
        return None, None, "not_found"
    source = entry.get("source", "")
    if source not in STREET_LEVEL_SOURCES:
        return None, None, "not_found"
    try:
        lat, lng = float(entry["lat"]), float(entry["lng"])
    except (TypeError, ValueError):
        return None, None, "rejected_coordinates"
    if not (math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None, "rejected_coordinates"
    parts = key.split("|")
    if len(parts) != 4:
        return None, None, "rejected_address"
    street, _, state, zipcode = parts
    state = state.strip().upper()
    if not street.strip() or is_po_box(street) or unclear_house_number(street):
        return None, None, "not_found"
    foreign = is_foreign_address(state, zipcode)
    country = (entry.get("country") or ("US" if not foreign else "")).upper()
    if foreign:
        if not country or country == "US":
            return None, None, "rejected_us_match_for_foreign"
    else:
        territory_country = state in US_TERRITORIES and country == state
        if (country != "US" and not territory_country) or not valid_for_state(lat, lng, state):
            return None, None, "rejected_out_of_us"
    if is_reviewed_wrong(key, lat, lng):
        return None, None, "rejected_reviewed_wrong"
    return lat, lng, source


_CITY_HOUSE_RE = re.compile(r"^(\d+[A-Z]?(?:[-/]\d+[A-Z]?)?(?: \d+/[1-9]\d*| ?[¼½¾])?) (.+)$")
_CITY_DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
_CITY_STREET_TYPES = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "BOULEVARD": "BLVD",
    "DRIVE": "DR", "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "PLACE": "PL",
    "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TERRACE": "TER", "SQUARE": "SQ",
    "TRAIL": "TRL", "PLAZA": "PLZ", "TURNPIKE": "TPKE", "EXPRESSWAY": "EXPY",
    "ALLEY": "ALY", "CRESCENT": "CRES", "POINT": "PT", "HEIGHTS": "HTS",
    "JUNCTION": "JCT", "RIDGE": "RDG", "ROUTE": "RTE", "EXTENSION": "EXT",
}


# Keep road words in order; format directions and suffix only.
def _city_road_tokens(street: str) -> tuple[str, ...]:
    tokens = street.upper().split()
    if not tokens:
        return ()
    directions = set(_CITY_DIRECTIONS.values())
    if len(tokens) > 2:
        for index in (0, len(tokens) - 1):
            word = tokens[index].replace(".", "")
            if word in _CITY_DIRECTIONS or word in directions:
                tokens[index] = _CITY_DIRECTIONS.get(word, word)
    suffix = len(tokens) - 2 if len(tokens) > 1 and tokens[-1] in directions else len(tokens) - 1
    word = tokens[suffix].rstrip(".")
    if word in _CITY_STREET_TYPES or word in _CITY_STREET_TYPES.values():
        tokens[suffix] = _CITY_STREET_TYPES.get(word, word)
    return tuple(tokens)


# A provider city counts only when other address parts agree.
def confirmed_city(key: str, entry: dict | None) -> str | None:
    if not isinstance(entry, dict) or entry.get("source") not in {"nominatim", "census"}:
        return None
    parts = key.split("|")
    if len(parts) != 4:
        return None
    street, city, state, zipcode = (" ".join(part.upper().split()) for part in parts)
    if state not in US_STATES or not re.fullmatch(r"\d{5}(?:-\d{4})?", zipcode):
        return None
    country = str(entry.get("country") or "").upper()
    if country != "US" and not (state in US_TERRITORIES and country == state):
        return None
    matched = entry.get("matched_address")
    fields = ("text", "house_number", "street", "city", "state", "zip", "source_url")
    if not isinstance(matched, dict) or any(
        not isinstance(matched.get(field), str) or not matched[field].strip() for field in fields
    ):
        return None
    try:
        source_url = urlsplit(matched["source_url"].strip())
        if source_url.scheme not in {"http", "https"} or not source_url.hostname:
            return None
    except ValueError:
        return None
    if any(char.isspace() for char in matched["source_url"].strip()):
        return None
    evidence = {field: " ".join(matched[field].upper().split()) for field in fields}
    if evidence["state"].removeprefix("US-") != state:
        return None
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", evidence["zip"]) or evidence["zip"][:5] != zipcode[:5]:
        return None
    house = _CITY_HOUSE_RE.fullmatch(street)
    if not house or house.group(1) != evidence["house_number"]:
        return None
    if _city_road_tokens(house.group(2)) != _city_road_tokens(evidence["street"]):
        return None
    lat, lng, source = accepted_coordinates(key, entry)
    if lat is None or lng is None or source not in {"nominatim", "census"}:
        return None
    actual = evidence["city"]
    return actual if actual != city and any(char.isalpha() for char in actual) else None


# Same-address corrections retained from the earlier cache review.
REVIEWED_POINTS = {row["address"]: (float(row["lat"]), float(row["lng"]), row["reason"])
                   for row in read_rows(RULES_DIR / "reviewed_points.csv")}

# Previously reviewed bad points must not reappear as address matches.
REVIEWED_WRONG_POINTS = {row["address"]: (float(row["lat"]), float(row["lng"]), row["reason"])
                         for row in read_rows(RULES_DIR / "reviewed_wrong_points.csv")}

