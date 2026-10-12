"""Geocode foreign addresses and unplaceable US ones."""
import time

from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    nominatim_international,
)
from fec.geocoding.locality import Locality
from fec.geocoding.places import in_us_bounds as _in_us_bounds


# geocode foreign address; accept only non-US results
def geocode_foreign(street, city, state, zipcode):
    queries = []
    if street:
        queries.append((street, "nominatim_intl"))
    if city:
        queries.append(("", "nominatim_intl_city"))
    for query_street, level in queries:
        lat, lng, country_code = nominatim_international(query_street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat is not None and country_code and country_code != "US":
            return lat, lng, country_code, level
    return None, None, None, "not_found"


# geocode abroad unless ZIP lies in state
def geocode_abroad_unless_us_zip(street, city, locality: Locality):
    if locality.zip_area is not None:
        return None, None, None, "not_found"
    return _geocode_international(street, city)


# last-resort international geocode; accept only confident foreign
def _geocode_international(street, city):
    if not street and not city:
        return None, None, None, "not_found"
    # drop suspect US state; it biases lookup
    lat, lng, country_code = nominatim_international(street, city, "", "")
    time.sleep(NOMINATIM_DELAY)
    if lat and country_code and country_code != "US" and not _in_us_bounds(lat, lng):
        return lat, lng, country_code, "nominatim_intl"
    return None, None, None, "not_found"
