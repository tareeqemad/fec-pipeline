"""One address query per provider, with bounded retries and Nominatim pacing."""

import os
import re
import time
from urllib.parse import urlparse

import requests

from fec.geocoding.validation import US_TERRITORIES, is_foreign_address


NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "FEC-Geocoder/1.0 (research project)"}
NOMINATIM_TIMEOUT = 10
NOMINATIM_RETRIES = 2
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_TIMEOUT = 10
CENSUS_RETRIES = 2

_AREA_TYPES = {
    "administrative", "boundary", "country", "state", "province", "region",
    "county", "city", "town", "village", "hamlet", "municipality", "borough",
    "district", "city_district", "suburb", "neighbourhood", "quarter",
    "postcode", "postal_code", "road", "street", "pedestrian", "residential",
}
_HOUSE_NUMBER = re.compile(r"^\s*(\d+[A-Z]?(?:[-/]\d+[A-Z]?)?(?:\s+\d/\d)?)\b", re.IGNORECASE)
_last_nominatim_request: float | None = None


class NominatimUnavailable(RuntimeError):
    """Nominatim failed temporarily; this is not an address miss."""


class CensusUnavailable(RuntimeError):
    """Census failed temporarily; this is not an address miss."""


# Compare the supplied and returned house numbers.
def _same_house_number(street: str, returned_number: str) -> bool:
    expected = _HOUSE_NUMBER.match(street)
    if expected is None:
        first = street.split()[0] if street.strip() else ""
        return not any(char.isdigit() for char in first) or first.upper() == returned_number.strip().upper()
    return expected.group(1).upper() == returned_number.strip().upper()


# Reject a different state or ZIP; allow postal city aliases.
def _same_us_place(state: str, zipcode: str, returned_state, returned_zip) -> bool:
    if state and returned_state:
        actual_state = str(returned_state).strip().upper().removeprefix("US-")
        if actual_state != state.strip().upper():
            return False
    if zipcode and returned_zip:
        if str(returned_zip).strip()[:5] != zipcode.strip()[:5]:
            return False
    return True


# Accept buildings and addresses, excluding street and area centres.
def _is_address_result(result: dict, street: str) -> bool:
    category = result.get("category", result.get("class", ""))
    kind = result.get("type", "")
    address_type = result.get("addresstype", "")
    if category in {"boundary", "highway", "landuse"} or address_type in _AREA_TYPES:
        return False
    if kind in _AREA_TYPES and category != "building":
        return False
    number = str((result.get("address") or {}).get("house_number") or "")
    if not _same_house_number(street, number):
        return False
    return bool(
        number or category in {"building", "amenity", "shop", "office", "tourism", "historic"}
        or kind in {"house", "building", "apartments"}
        or address_type in {"house", "building"}
    )


# Space every request and retry using the same clock.
def _wait_for_nominatim(url: str) -> None:
    global _last_nominatim_request
    public = urlparse(url).hostname == "nominatim.openstreetmap.org"
    minimum = 15.0 if public else 1.0
    interval = max(minimum, float(os.environ.get("NOMINATIM_DELAY_SECONDS", minimum)))
    if _last_nominatim_request is not None:
        remaining = interval - (time.monotonic() - _last_nominatim_request)
        if remaining > 0:
            time.sleep(remaining)
    _last_nominatim_request = time.monotonic()


# Look up the supplied address; omit the US filter for foreign addresses.
def nominatim(street: str, city: str, state: str, zipcode: str) -> dict | None:
    if not street:
        return None
    params = {
        "q": ", ".join(part for part in (street, city, state, zipcode) if part),
        "format": "jsonv2", "limit": 1, "addressdetails": 1,
    }
    foreign = is_foreign_address(state, zipcode)
    if not foreign:
        state_code = state.strip().upper()
        params["countrycodes"] = f"us,{state_code.lower()}" if state_code in US_TERRITORIES else "us"
    url = os.environ.get("NOMINATIM_URL", NOMINATIM_URL)
    headers = {
        **NOMINATIM_HEADERS,
        "User-Agent": os.environ.get("NOMINATIM_USER_AGENT", NOMINATIM_HEADERS["User-Agent"]),
    }
    last_error = "Nominatim unavailable"
    for attempt in range(1 + NOMINATIM_RETRIES):
        _wait_for_nominatim(url)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=NOMINATIM_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"Nominatim HTTP {response.status_code}"
            elif not response.ok:
                raise NominatimUnavailable(f"Nominatim HTTP {response.status_code}")
            else:
                results = response.json()
                if not isinstance(results, list):
                    raise ValueError("expected a result list")
                if not results:
                    return None
                result = results[0]
                if not _is_address_result(result, street):
                    return None
                address = result.get("address") or {}
                if not foreign and not _same_us_place(
                    state, zipcode,
                    address.get("ISO3166-2-lvl4") or address.get("state_code"),
                    address.get("postcode"),
                ):
                    return None
                country = str(address.get("country_code") or "").upper() or None
                return {
                    "lat": float(result["lat"]), "lng": float(result["lon"]), "country": country,
                    "matched_address": {
                        "text": result.get("display_name") or "",
                        "house_number": address.get("house_number") or "",
                        "street": address.get("road") or "",
                        "city": address.get("city") or address.get("town") or address.get("village") or "",
                        "state": (address.get("ISO3166-2-lvl4") or address.get("state_code") or "").removeprefix("US-"),
                        "zip": address.get("postcode") or "",
                        "source_url": requests.Request("GET", url, params=params).prepare().url,
                    },
                }
        except requests.exceptions.RequestException as error:
            last_error = f"Nominatim {type(error).__name__}"
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise NominatimUnavailable("Invalid Nominatim response") from error
        if attempt < NOMINATIM_RETRIES:
            time.sleep(2 ** attempt)
    raise NominatimUnavailable(last_error)


# Match the supplied US address with Census.
def census(street: str, city: str, state: str, zipcode: str) -> dict | None:
    if not street or is_foreign_address(state, zipcode):
        return None
    params = {
        "address": ", ".join(part for part in (street, city, state, zipcode) if part),
        "benchmark": "Public_AR_Current", "format": "json",
    }
    last_error = "Census unavailable"
    for attempt in range(1 + CENSUS_RETRIES):
        try:
            response = requests.get(CENSUS_URL, params=params, timeout=CENSUS_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"Census HTTP {response.status_code}"
            elif not response.ok:
                raise CensusUnavailable(f"Census HTTP {response.status_code}")
            else:
                matches = response.json()["result"]["addressMatches"]
                if not isinstance(matches, list):
                    raise ValueError("expected an address match list")
                if not matches:
                    return None
                match = matches[0]
                street_text = str(match.get("matchedAddress") or "").split(",", 1)[0].strip()
                returned = _HOUSE_NUMBER.match(street_text)
                first = street_text.split()
                number = returned.group(1) if returned else (first[0] if first else "")
                if not _same_house_number(street, number):
                    return None
                components = match.get("addressComponents") or {}
                if not _same_us_place(state, zipcode, components.get("state"), components.get("zip")):
                    return None
                coordinates = match["coordinates"]
                return {
                    "lat": float(coordinates["y"]), "lng": float(coordinates["x"]), "country": "US",
                    "matched_address": {
                        "text": match.get("matchedAddress") or "",
                        "house_number": number,
                        "street": street_text[returned.end():].strip() if returned else "",
                        "city": components.get("city") or "",
                        "state": components.get("state") or "",
                        "zip": components.get("zip") or "",
                        "source_url": requests.Request("GET", CENSUS_URL, params=params).prepare().url,
                    },
                }
        except requests.exceptions.RequestException as error:
            last_error = f"Census {type(error).__name__}"
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise CensusUnavailable("Invalid Census response") from error
        if attempt < CENSUS_RETRIES:
            time.sleep(2 ** attempt)
    raise CensusUnavailable(last_error)
