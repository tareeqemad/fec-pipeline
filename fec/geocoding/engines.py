"""Geocoding engines: Census, Nominatim (US and international) and city-level fallback."""

import time

import requests

from fec.log import get_logger

from .places import STATE_NAMES, choose_town, result_point

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "FEC-Geocoder/1.0 (research project)"}
NOMINATIM_DELAY = 1.05  # seconds; policy is max 1 req/sec
NOMINATIM_RETRIES = 2  # retry on 429/timeout
NOMINATIM_TIMEOUT = 10  # seconds
CENSUS_URL = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
CENSUS_TIMEOUT = 10
CENSUS_RETRIES = 2
# unauthorized, forbidden (Nominatim's block for a client over its usage policy), proxy authentication
_BLOCKED_STATUSES = frozenset({401, 403, 407})


class NominatimUnavailable(RuntimeError):
    """Nominatim could not be reached."""


class CensusUnavailable(RuntimeError):
    """Census Geocoder could not be reached."""


# geocode a US street via Census Geocoder, retrying on failure
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


# geocode a US address via Nominatim, structured then free-form
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


# search a street via Nominatim restricted to a bounding box
def nominatim_within(street: str, city: str, state: str,
                     box: tuple[float, float, float, float]) -> tuple:
    """Search a US street in the filed city only inside box (south, west, north, east); asks whether the address exists in the filed ZIP at all."""
    south, west, north, east = box
    query = ", ".join(part for part in (street, city, state) if part)
    return _nominatim_request(params={
        "q": query,
        "format": "json", "limit": 1, "countrycodes": "us",
        "viewbox": f"{west},{north},{east},{south}", "bounded": 1,
    })


# geocode via Nominatim with no country restriction, last resort
def nominatim_international(street: str, city: str, state: str, zipcode: str) -> tuple:
    """Geocode via Nominatim with no country restriction (foreign addresses, and the last resort for US-labelled ones)."""
    query = ", ".join(part for part in (street, city, state) if part)
    if zipcode:
        query += f" {zipcode}"
    return _nominatim_request(params={
        "q": query,
        "format": "json", "limit": 1,
    })


# geocode to the filed town's own point as a fallback
def city_level(city: str, state: str, zipcode: str = "",
               near: tuple[float, float] | None = None) -> tuple:
    """The filed town's own point (PO boxes, or when street-level fails): (lat, lng, ISO-2 country) or Nones.

    A structured search (city=, the state's full name, country us, no free-text
    ', USA' word; postalcode only when given, i.e. when the ZIP has a centroid)
    that keeps only a settlement named like the city inside the state, so a county,
    road, building or a POI named '... USA' is never taken for the town. near (the
    filed ZIP's area) picks among same-name towns in one state. A free-text
    'CITY, STATE' retry found no town on the 380 re-checked keys of 2026-09-24
    (JAMAICA NY gives only its railway stations), so there is none.
    """
    if not str(city or "").strip():
        return None, None, None
    params = {"city": city, "format": "jsonv2", "limit": 10, "countrycodes": "us", "namedetails": 1}
    if STATE_NAMES.get(state):
        params["state"] = STATE_NAMES[state]
    if zipcode:
        params["postalcode"] = zipcode
    town = choose_town(_nominatim_results(params), city, state, near)
    if town is None:
        return None, None, None
    lat, lng = result_point(town)
    country_code = ((town.get("address") or {}).get("country_code") or "").upper() or None
    return lat, lng, country_code


# run one Nominatim request and return its top coordinates
def _nominatim_request(params: dict, _retries=NOMINATIM_RETRIES):
    """One Nominatim request, retried on 429/5xx/timeout only; returns (lat, lng, ISO-2 country) or Nones."""
    results = _nominatim_results(params, _retries)
    if not results:
        return None, None, None
    first_result = results[0]
    # country_code tells a genuinely foreign address from a wrong-state US match
    country_code = (first_result.get("address", {}).get("country_code") or "").upper() or None
    return float(first_result["lat"]), float(first_result["lon"]), country_code


# fetch all Nominatim results, retrying on rate limit or error
def _nominatim_results(params: dict, _retries=NOMINATIM_RETRIES) -> list[dict]:
    """Every result of one Nominatim search, retried on 429/5xx/timeout only; [] when nothing is found."""
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

            if response.status_code in _BLOCKED_STATUSES:
                # a blocked client or proxy answers every search alike: an outage, not a
                # miss (read as 'not found' it would end lookups and re-checks for good)
                raise NominatimUnavailable(f"Nominatim HTTP {response.status_code}")

            if response.ok:
                results = response.json()
                if not isinstance(results, list):
                    raise ValueError(f"unexpected Nominatim answer: {str(results)[:200]}")
                return results

            # any other client error: genuinely not found, no retry
            return []

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
