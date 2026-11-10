"""Accept address coordinates from the geocode cache."""

import math
import re

from fec.vocabulary.geography import US_STATE_BBOX, US_STATES

EARTH_RADIUS_MILES = 3958.8

STREET_LEVEL_SOURCES = {
    "nominatim",
    "census",
    "google",
    "manual_census",
    "manual_verified",
    "nominatim_intl",
}

US_TERRITORIES = {"AS", "GU", "MP", "PR", "VI"}

# A Canadian or British postcode.
_FOREIGN_POSTCODE = re.compile(r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}")


def is_po_box(street: str) -> bool:
    """Return whether the street is a PO box."""
    return bool(re.match(r"^P[.\s]*O[.\s]*BOX\b", street.strip(), re.IGNORECASE))


def unclear_house_number(street: str) -> bool:
    """Return whether the house number starts with punctuation."""
    return bool(re.match(r"^[^A-Z0-9\s]+\d", street.strip(), re.IGNORECASE))


def is_foreign_address(state: str, zipcode: str) -> bool:
    """Return whether the state or postcode marks the address as outside the US."""
    state, zipcode = str(state or "").strip().upper(), str(zipcode or "").strip().upper()
    if state:
        return state not in US_STATES or bool(_FOREIGN_POSTCODE.fullmatch(zipcode))
    if zipcode.endswith(".0"):
        zipcode = zipcode[:-2].zfill(5)
    return not bool(re.fullmatch(r"\d{5}(?:-?\d{4})?", zipcode))


def great_circle(first: tuple, second: tuple, radius: float) -> float:
    """Return the great-circle distance between two (lat, lng) points on a sphere of this radius."""
    first_lat, first_lng = map(math.radians, first)
    second_lat, second_lng = map(math.radians, second)
    haversine = math.sin((second_lat - first_lat) / 2) ** 2
    haversine += math.cos(first_lat) * math.cos(second_lat) * math.sin((second_lng - first_lng) / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def valid_for_state(lat: float, lng: float, state: str) -> bool:
    """Return whether the point lies within the state's bounding box, or any state's if unknown."""
    state = state.strip().upper()
    bounds = [US_STATE_BBOX[state]] if state in US_STATE_BBOX else US_STATE_BBOX.values()
    return any(south - 1 <= lat <= north + 1 and west - 1 <= lng <= east + 1 for south, north, west, east in bounds)


def accepted_coordinates(key: str, entry: dict | None) -> tuple:
    """Return (lat, lng, source) for a cached entry, or (None, None, reason) when it is rejected."""
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
    return lat, lng, source
