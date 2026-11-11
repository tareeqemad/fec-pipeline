"""One address query per provider (OpenStreetMap's Nominatim, then Census), with bounded retries and pacing."""

import os
import re
import time

import requests

from fec.geocoding.coordinate_checks import US_TERRITORIES, is_foreign_address
from fec.vocabulary.tables import words

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "FEC-Geocoder/1.0 (research project)"
NOMINATIM_SECONDS_BETWEEN_REQUESTS = 15.0
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
TIMEOUT = 10
RETRIES = 2

# Nominatim answers of these kinds are an area or a road, not the address itself.
_AREA_TYPES = words("nominatim_area_types")
_HOUSE_NUMBER = re.compile(r"^\s*(\d+[A-Z]?(?:[-/]\d+[A-Z]?)?(?:\s+\d/\d)?)\b", re.IGNORECASE)
_last_nominatim_request = float("-inf")


class GeocoderUnavailable(RuntimeError):
    """A provider failed temporarily; this is not an address miss."""


def _seconds_between_nominatim_requests(url: str) -> float:
    # The public server asks for slow use; one's own server (NOMINATIM_URL) may go faster, never under a second.
    minimum = NOMINATIM_SECONDS_BETWEEN_REQUESTS if "nominatim.openstreetmap.org" in url else 1.0
    return max(minimum, float(os.environ.get("NOMINATIM_DELAY_SECONDS", minimum)))


def _get_json(provider: str, url: str, params: dict, headers: dict | None = None) -> object:
    global _last_nominatim_request
    last_error = f"{provider} unavailable"
    for attempt in range(1 + RETRIES):
        if provider == "Nominatim":
            wait = _seconds_between_nominatim_requests(url) - (time.monotonic() - _last_nominatim_request)
            if wait > 0:
                time.sleep(wait)
            _last_nominatim_request = time.monotonic()
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"{provider} HTTP {response.status_code}"
            elif not response.ok:
                raise GeocoderUnavailable(f"{provider} HTTP {response.status_code}")
            else:
                return response.json()
        except requests.exceptions.RequestException as error:
            last_error = f"{provider} {type(error).__name__}"
        if attempt < RETRIES:
            time.sleep(2**attempt)
    raise GeocoderUnavailable(last_error)


def _is_same_house_number(street: str, returned_number: str) -> bool:
    expected = _HOUSE_NUMBER.match(street)
    if expected is None:
        first_word = street.split()[0] if street.strip() else ""
        return not any(char.isdigit() for char in first_word) or first_word.upper() == returned_number.strip().upper()
    return expected.group(1).upper() == returned_number.strip().upper()


def _is_same_us_place(state: str, zipcode: str, returned_state: object, returned_zip: object) -> bool:
    if state and returned_state:
        actual_state = str(returned_state).strip().upper().removeprefix("US-")
        if actual_state != state.strip().upper():
            return False
    if not (zipcode and returned_zip):
        return True
    return str(returned_zip).strip()[:5] == zipcode.strip()[:5]


def _is_address_result(result: dict, street: str) -> bool:
    category = result.get("category", result.get("class", ""))
    kind = result.get("type", "")
    address_type = result.get("addresstype", "")
    if category in {"boundary", "highway", "landuse"} or address_type in _AREA_TYPES:
        return False
    if kind in _AREA_TYPES and category != "building":
        return False
    number = str((result.get("address") or {}).get("house_number") or "")
    if not _is_same_house_number(street, number):
        return False
    return bool(
        number
        or category in {"building", "amenity", "shop", "office", "tourism", "historic"}
        or kind in {"house", "building", "apartments"}
        or address_type in {"house", "building"}
    )


def _read_nominatim_results(results: object, street: str, state: str, zipcode: str) -> dict | None:
    if not isinstance(results, list):
        raise ValueError("expected a result list")
    if not results or not _is_address_result(results[0], street):
        return None
    result = results[0]
    address = result.get("address") or {}
    returned_state = address.get("ISO3166-2-lvl4") or address.get("state_code")
    foreign = is_foreign_address(state, zipcode)
    if not foreign and not _is_same_us_place(state, zipcode, returned_state, address.get("postcode")):
        return None
    country = str(address.get("country_code") or "").upper() or None
    return {"lat": float(result["lat"]), "lng": float(result["lon"]), "source": "nominatim", "country": country}


def nominatim(street: str, city: str, state: str, zipcode: str) -> dict | None:
    """Look up the address in OpenStreetMap; no US filter for foreign addresses."""
    params = {
        "q": ", ".join(part for part in (street, city, state, zipcode) if part),
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
    }
    if not is_foreign_address(state, zipcode):
        state_code = state.strip().upper()
        params["countrycodes"] = f"us,{state_code.lower()}" if state_code in US_TERRITORIES else "us"
    url = os.environ.get("NOMINATIM_URL", NOMINATIM_URL)
    headers = {"User-Agent": os.environ.get("NOMINATIM_USER_AGENT", NOMINATIM_USER_AGENT)}
    results = _get_json("Nominatim", url, params, headers)
    try:
        return _read_nominatim_results(results, street, state, zipcode)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise GeocoderUnavailable("Invalid Nominatim response") from error


def _read_census_match(data: dict, street: str, state: str, zipcode: str) -> dict | None:
    matches = data["result"]["addressMatches"]
    if not isinstance(matches, list):
        raise ValueError("expected an address match list")
    if not matches:
        return None
    match = matches[0]
    street_text = str(match.get("matchedAddress") or "").split(",", 1)[0].strip()
    returned = _HOUSE_NUMBER.match(street_text)
    words = street_text.split()
    number = returned.group(1) if returned else (words[0] if words else "")
    if not _is_same_house_number(street, number):
        return None
    components = match.get("addressComponents") or {}
    if not _is_same_us_place(state, zipcode, components.get("state"), components.get("zip")):
        return None
    coordinates = match["coordinates"]
    return {"lat": float(coordinates["y"]), "lng": float(coordinates["x"]), "source": "census", "country": "US"}


def census(street: str, city: str, state: str, zipcode: str) -> dict | None:
    """Match a US address with the Census geocoder."""
    if is_foreign_address(state, zipcode):
        return None
    params = {
        "address": ", ".join(part for part in (street, city, state, zipcode) if part),
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    data = _get_json("Census", CENSUS_URL, params)
    try:
        return _read_census_match(data, street, state, zipcode)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise GeocoderUnavailable("Invalid Census response") from error
