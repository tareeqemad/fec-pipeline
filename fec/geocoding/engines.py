"""
geocode/engines.py — Geocoding API wrappers.

Three strategies, tried in order:
    1. Nominatim (OpenStreetMap) — free, 1 req/sec
    2. Google Maps — paid, fast, needs API key
    3. City-level fallback — free, returns city center coords
"""

from typing import Optional
import time
import requests

from fec.log import get_logger

logger = get_logger(__name__)

# ── Nominatim config ──

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "FEC-Geocoder/1.0 (research project)"}
NOMINATIM_DELAY   = 1.05        # seconds (policy: max 1 req/sec)
NOMINATIM_RETRIES = 2           # retry on 429/timeout
NOMINATIM_TIMEOUT = 10          # seconds

# ── Google config ──

GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"

# Permanent Google errors — stop retrying if any of these
_GOOGLE_FATAL_STATUSES = {"REQUEST_DENIED", "OVER_DAILY_LIMIT", "OVER_QUERY_LIMIT"}

# Track Google API health (module-level flag)
_google_disabled = False


def nominatim(street: str, city: str, state: str, zipcode: str) -> Optional[dict]:
    """
    Geocode a US street address via OpenStreetMap Nominatim.

    Tries structured query first, then free-form.
    Retries on 429/timeout (transient errors).

    Returns (lat, lng) or (None, None).
    """
    # Try 1: structured
    res = _nominatim_request(params={
        "street": street, "city": city, "state": state,
        "postalcode": zipcode, "country": "US",
        "format": "json", "limit": 1,
    })
    if res[0] is not None:
        return res

    # Try 2: free-form
    time.sleep(NOMINATIM_DELAY)
    return _nominatim_request(params={
        "q": f"{street}, {city}, {state} {zipcode}, USA",
        "format": "json", "limit": 1, "countrycodes": "us",
    })


def nominatim_international(street: str, city: str, state: str, zipcode: str) -> Optional[dict]:
    """
    Geocode a non-US address via Nominatim (no country restriction).

    Used as last-resort for addresses with foreign cities (Jerusalem, Montreal, etc.)
    Returns (lat, lng) or (None, None).
    """
    query = f"{street}, {city}"
    if state:
        query += f", {state}"
    if zipcode:
        query += f" {zipcode}"
    return _nominatim_request(params={
        "q": query,
        "format": "json", "limit": 1,
    })


def google(street: str, city: str, state: str, zipcode: str, api_key: str) -> Optional[dict]:
    """
    Geocode via Google Maps Geocoding API.

    Returns (lat, lng) or (None, None).
    Disables itself on permanent errors (bad key, quota exceeded).
    """
    global _google_disabled
    if _google_disabled:
        return None, None, None

    address = f"{street}, {city}, {state} {zipcode}, USA"
    try:
        resp = requests.get(
            GOOGLE_URL,
            params={"address": address, "key": api_key},
            timeout=10,
        )
        if not resp.ok:
            logger.warning("Google API HTTP %d — disabling", resp.status_code)
            _google_disabled = True
            return None, None, None

        data = resp.json()
        status = data.get("status", "")

        if status == "OK" and data.get("results"):
            res0 = data["results"][0]
            loc = res0["geometry"]["location"]
            cc = None
            for comp in res0.get("address_components", []):
                if "country" in comp.get("types", []):
                    cc = (comp.get("short_name") or "").upper() or None
                    break
            return float(loc["lat"]), float(loc["lng"]), cc

        if status in _GOOGLE_FATAL_STATUSES:
            msg = data.get("error_message", status)
            logger.warning("Google API: %s — disabling for this run", msg)
            _google_disabled = True
            return None, None, None

        # ZERO_RESULTS is normal (address not found)
        return None, None, None

    except requests.exceptions.Timeout:
        logger.warning("Google API timeout")
        return None, None, None
    except requests.exceptions.ConnectionError:
        logger.warning("Google API connection error — disabling")
        _google_disabled = True
        return None, None, None
    except Exception as e:
        logger.warning("Google API unexpected error: %s", e)
        return None, None, None


def city_level(city: str, state: str, zipcode: str) -> Optional[dict]:
    """
    Fallback: geocode city + state + ZIP only.

    Useful for PO Boxes or when street-level geocoding fails.
    Returns (lat, lng) or (None, None).
    """
    query = f"{city}, {state} {zipcode}, USA"
    return _nominatim_request(params={
        "q": query, "format": "json", "limit": 1, "countrycodes": "us",
    })


# ── Internal helpers ──

def _nominatim_request(params: dict, _retries=NOMINATIM_RETRIES):
    """
    Send a single Nominatim request with retry on transient errors.

    Returns (lat, lng, country_code) or (None, None, None). country_code is the
    ISO-3166 alpha-2 (e.g. 'US', 'IL') parsed from addressdetails — used to tell
    a genuinely foreign address apart from a wrong-state US match.

    Retries on: 429 (rate limit), timeout, connection error.
    Does NOT retry on: 200 with no results (genuinely not found).
    """
    params = {**params, "addressdetails": 1}
    for attempt in range(1 + _retries):
        try:
            resp = requests.get(
                NOMINATIM_URL, params=params,
                headers=NOMINATIM_HEADERS, timeout=NOMINATIM_TIMEOUT,
            )

            # Rate limited — wait and retry
            if resp.status_code == 429:
                wait = 2 ** attempt * NOMINATIM_DELAY
                logger.debug("Nominatim 429 — waiting %.1fs (attempt %d)", wait, attempt + 1)
                time.sleep(wait)
                continue

            # Server error — retry
            if resp.status_code >= 500:
                logger.debug("Nominatim %d — retrying (attempt %d)", resp.status_code, attempt + 1)
                time.sleep(NOMINATIM_DELAY * 2)
                continue

            # Success — parse result
            if resp.ok and resp.json():
                r = resp.json()[0]
                cc = (r.get("address", {}).get("country_code") or "").upper() or None
                return float(r["lat"]), float(r["lon"]), cc

            # 200 but no results — genuinely not found (don't retry)
            return None, None, None

        except requests.exceptions.Timeout:
            logger.debug("Nominatim timeout (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY)
            continue

        except requests.exceptions.ConnectionError:
            logger.debug("Nominatim connection error (attempt %d/%d)", attempt + 1, 1 + _retries)
            time.sleep(NOMINATIM_DELAY * 2)
            continue

        except Exception as e:
            logger.warning("Nominatim unexpected error: %s", e)
            return None, None, None

    # All retries exhausted
    logger.debug("Nominatim: all %d attempts failed", 1 + _retries)
    return None, None, None
