"""Nominatim geocoding requests."""

import time

import requests

from fec.log import get_logger

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "FEC-Geocoder/1.0 (research project)"}
NOMINATIM_DELAY = 1.05  # seconds; policy is max 1 req/sec
NOMINATIM_RETRIES = 2  # retry on 429/timeout
NOMINATIM_TIMEOUT = 10  # seconds
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_TIMEOUT = 10
CENSUS_RETRIES = 2


class NominatimUnavailable(RuntimeError):
    """Nominatim could not be reached."""


class CensusUnavailable(RuntimeError):
    """Census Geocoder could not be reached."""


def census(street: str, city: str, state: str, zipcode: str) -> tuple:
    """Geocode a US street with the official Census address ranges."""
    address = f"{street}, {city}, {state} {zipcode}, USA"
    params = {
        "address": address,
        "benchmark": "Public_AR_Current",
        "format": "json",
    }
    last_error = "temporary Census Geocoder failure"

    for attempt in range(1 + CENSUS_RETRIES):
        try:
            response = requests.get(CENSUS_URL, params=params, timeout=CENSUS_TIMEOUT)
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"Census HTTP {response.status_code}"
                time.sleep(2 ** attempt)
                continue
            if not response.ok:
                return None, None, None

            matches = response.json().get("result", {}).get("addressMatches", [])
            if not matches:
                return None, None, None
            coordinates = matches[0]["coordinates"]
            return float(coordinates["y"]), float(coordinates["x"]), "US"

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
            last_error = str(error)
            time.sleep(2 ** attempt)
        except (KeyError, TypeError, ValueError) as error:
            raise CensusUnavailable(str(error)) from error

    raise CensusUnavailable(last_error)


def nominatim(street: str, city: str, state: str, zipcode: str) -> tuple:
    """Geocode a US street address via Nominatim: structured query first, then free-form."""
    result = _nominatim_request(params={
        "street": street, "city": city, "state": state,
        "postalcode": zipcode, "country": "US",
        "format": "json", "limit": 1,
    })
    if result[0] is not None:
        return result

    time.sleep(NOMINATIM_DELAY)
    return _nominatim_request(params={
        "q": f"{street}, {city}, {state} {zipcode}, USA",
        "format": "json", "limit": 1, "countrycodes": "us",
    })


def nominatim_international(street: str, city: str, state: str, zipcode: str) -> tuple:
    """Geocode via Nominatim with no country restriction (last resort for foreign cities)."""
    query = f"{street}, {city}"
    if state:
        query += f", {state}"
    if zipcode:
        query += f" {zipcode}"
    return _nominatim_request(params={
        "q": query,
        "format": "json", "limit": 1,
    })


def city_level(city: str, state: str, zipcode: str) -> tuple:
    """Geocode city + state + ZIP only (PO boxes, or when street-level fails)."""
    query = f"{city}, {state} {zipcode}, USA"
    return _nominatim_request(params={
        "q": query, "format": "json", "limit": 1, "countrycodes": "us",
    })


def _nominatim_request(params: dict, _retries=NOMINATIM_RETRIES):
    """One Nominatim request, retried on 429/5xx/timeout only; returns (lat, lng, ISO-2 country) or Nones."""
    params = {**params, "addressdetails": 1}
    last_error = "temporary Nominatim failure"
    for attempt in range(1 + _retries):
        try:
            response = requests.get(
                NOMINATIM_URL, params=params,
                headers=NOMINATIM_HEADERS, timeout=NOMINATIM_TIMEOUT,
            )

            if response.status_code == 429:
                last_error = "Nominatim rate limit"
                wait = 2 ** attempt * NOMINATIM_DELAY
                logger.debug("Nominatim 429 - waiting %.1fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
                last_error = f"Nominatim HTTP {response.status_code}"
                logger.debug("Nominatim %d - retrying (attempt %d)", response.status_code, attempt + 1)
                time.sleep(NOMINATIM_DELAY * 2)
                continue

            if response.ok and response.json():
                first_result = response.json()[0]
                # country_code tells a genuinely foreign address from a wrong-state US match
                country_code = (first_result.get("address", {}).get("country_code") or "").upper() or None
                return float(first_result["lat"]), float(first_result["lon"]), country_code

            # 200 with no results: genuinely not found, no retry
            return None, None, None

        except requests.exceptions.Timeout:
            last_error = "Nominatim timeout"
            logger.debug("Nominatim timeout (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY)
            continue

        except requests.exceptions.ConnectionError:
            last_error = "Nominatim connection error"
            logger.debug("Nominatim connection error (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY * 2)
            continue

        except Exception as error:
            raise NominatimUnavailable(str(error)) from error

    logger.debug("Nominatim: all %d attempts failed", 1 + _retries)
    raise NominatimUnavailable(last_error)
