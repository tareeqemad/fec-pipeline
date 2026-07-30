"""Geocoding API wrappers, tried in order: Nominatim, Google, city-level fallback."""

import time

import requests

from fec.log import get_logger

logger = get_logger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "FEC-Geocoder/1.0 (research project)"}
NOMINATIM_DELAY = 1.05  # seconds; policy is max 1 req/sec
NOMINATIM_RETRIES = 2  # retry on 429/timeout
NOMINATIM_TIMEOUT = 10  # seconds

GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# permanent Google errors: stop retrying for the rest of the run
_GOOGLE_FATAL_STATUSES = {"REQUEST_DENIED", "OVER_DAILY_LIMIT", "OVER_QUERY_LIMIT"}

_google_disabled = False


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


def google(street: str, city: str, state: str, zipcode: str, api_key: str) -> tuple:
    """Geocode via Google Maps; disables itself for the run on permanent errors (bad key, quota)."""
    global _google_disabled
    if _google_disabled:
        return None, None, None

    address = f"{street}, {city}, {state} {zipcode}, USA"
    try:
        response = requests.get(
            GOOGLE_URL,
            params={"address": address, "key": api_key},
            timeout=10,
        )
        if not response.ok:
            logger.warning("Google API HTTP %d - disabling", response.status_code)
            _google_disabled = True
            return None, None, None

        data = response.json()
        status = data.get("status", "")

        if status == "OK" and data.get("results"):
            first_result = data["results"][0]
            location = first_result["geometry"]["location"]
            country_code = None
            for component in first_result.get("address_components", []):
                if "country" in component.get("types", []):
                    country_code = (component.get("short_name") or "").upper() or None
                    break
            return float(location["lat"]), float(location["lng"]), country_code

        if status in _GOOGLE_FATAL_STATUSES:
            message = data.get("error_message", status)
            logger.warning("Google API: %s - disabling for this run", message)
            _google_disabled = True
            return None, None, None

        # ZERO_RESULTS is normal (address not found)
        return None, None, None

    except requests.exceptions.Timeout:
        logger.warning("Google API timeout")
        return None, None, None
    except requests.exceptions.ConnectionError:
        logger.warning("Google API connection error - disabling")
        _google_disabled = True
        return None, None, None
    except Exception as error:
        logger.warning("Google API unexpected error: %s", error)
        return None, None, None


def city_level(city: str, state: str, zipcode: str) -> tuple:
    """Geocode city + state + ZIP only (PO boxes, or when street-level fails)."""
    query = f"{city}, {state} {zipcode}, USA"
    return _nominatim_request(params={
        "q": query, "format": "json", "limit": 1, "countrycodes": "us",
    })


def _nominatim_request(params: dict, _retries=NOMINATIM_RETRIES):
    """One Nominatim request, retried on 429/5xx/timeout only; returns (lat, lng, ISO-2 country) or Nones."""
    params = {**params, "addressdetails": 1}
    for attempt in range(1 + _retries):
        try:
            response = requests.get(
                NOMINATIM_URL, params=params,
                headers=NOMINATIM_HEADERS, timeout=NOMINATIM_TIMEOUT,
            )

            if response.status_code == 429:
                wait = 2 ** attempt * NOMINATIM_DELAY
                logger.debug("Nominatim 429 - waiting %.1fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue

            if response.status_code >= 500:
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
            logger.debug("Nominatim timeout (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY)
            continue

        except requests.exceptions.ConnectionError:
            logger.debug("Nominatim connection error (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY * 2)
            continue

        except Exception as error:
            logger.warning("Nominatim unexpected error: %s", error)
            return None, None, None

    logger.debug("Nominatim: all %d attempts failed", 1 + _retries)
    return None, None, None
