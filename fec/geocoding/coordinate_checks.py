"""Decide which addresses can be looked up and which coordinates are accepted."""

import math
import re

from fec.vocabulary.geography import US_STATE_BBOX, US_STATES

EARTH_RADIUS_MILES = 3958.8

STREET_LEVEL_SOURCES = {"nominatim", "census", "google", "manual_census", "manual_verified", "nominatim_intl"}

US_TERRITORIES = {"AS", "GU", "MP", "PR", "VI"}

# A Canadian or British postcode.
_FOREIGN_POSTCODE = re.compile(r"[ABCEGHJ-NPRSTVXY]\d[A-Z]\s?\d[A-Z]\d|[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}")


def is_street_address(street: str) -> bool:
    """Return whether the street can have a map point: not empty, not a PO box, a clear house number."""
    street = street.strip()
    is_po_box = re.match(r"^P[.\s]*O[.\s]*BOX\b", street, re.IGNORECASE)
    starts_with_punctuation = re.match(r"^[^A-Z0-9\s]+\d", street, re.IGNORECASE)
    return bool(street) and not is_po_box and not starts_with_punctuation


def is_foreign_address(state: str, zipcode: str) -> bool:
    """Return whether the state or postcode marks the address as outside the US."""
    state, zipcode = state.strip().upper(), zipcode.strip().upper()
    if state:
        return state not in US_STATES or bool(_FOREIGN_POSTCODE.fullmatch(zipcode))
    return not re.fullmatch(r"\d{5}(?:-?\d{4})?", zipcode)


def great_circle(first: tuple, second: tuple, radius: float) -> float:
    """Return the great-circle distance between two (lat, lng) points on a sphere of this radius."""
    first_lat, first_lng = map(math.radians, first)
    second_lat, second_lng = map(math.radians, second)
    haversine = math.sin((second_lat - first_lat) / 2) ** 2
    haversine += math.cos(first_lat) * math.cos(second_lat) * math.sin((second_lng - first_lng) / 2) ** 2
    return radius * 2 * math.asin(min(1.0, math.sqrt(haversine)))


def _is_within_state(lat: float, lng: float, state: str) -> bool:
    # One degree of margin around the state's bounding box; any state's box when the state is unknown.
    bounds = [US_STATE_BBOX[state]] if state in US_STATE_BBOX else US_STATE_BBOX.values()
    return any(south - 1 <= lat <= north + 1 and west - 1 <= lng <= east + 1 for south, north, west, east in bounds)


def accepted_coordinates(key: str, entry: dict | None) -> tuple[float, float] | None:
    """Return the (lat, lng) of a cache entry, or None when the address or the point fails a check."""
    if not entry or entry.get("lat") is None or entry.get("lng") is None:
        return None
    if entry.get("source") not in STREET_LEVEL_SOURCES or key.count("|") != 3:
        return None
    street, _, state, zipcode = key.split("|")
    lat, lng = float(entry["lat"]), float(entry["lng"])
    if not is_street_address(street) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    state = state.strip().upper()
    country = (entry.get("country") or "US").upper()
    if is_foreign_address(state, zipcode):
        # A foreign address must have been matched abroad.
        accepted = country != "US"
    else:
        in_us = country == "US" or (state in US_TERRITORIES and country == state)
        accepted = in_us and _is_within_state(lat, lng, state)
    return (lat, lng) if accepted else None
