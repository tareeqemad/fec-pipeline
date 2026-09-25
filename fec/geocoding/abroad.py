"""Geocode foreign addresses, and US-labelled ones no US engine can place."""
import time

from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    nominatim_international,
)
from fec.geocoding.locality import _Locality
from fec.geocoding.places import in_us_bounds as _in_us_bounds


# geocode a foreign address using only the international engine
def _geocode_foreign(street, city, state, zipcode):
    """A foreign address (no US state, or a non-US postal code): international engine only, and only a non-US result is accepted."""
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


# try international geocoding unless the filed ZIP matches the state
def _geocode_abroad_unless_us_zip(street, city, locality: _Locality):
    """The international last resort, skipped when the filed ZIP lies in the filed state.

    Such a filing is in the US even when its town is not found: JAMAICA NY is no OSM
    settlement, and 'PO BOX 5, JAMAICA' searched abroad is the island."""
    if locality.zip_area is not None:
        return None, None, None, "not_found"
    return _geocode_international(street, city)


# last-resort international geocode, accepted only if confidently foreign
def _geocode_international(street, city):
    """Last resort without a US restriction; accept only a confidently foreign result (non-US country AND coords outside the US), else not_found."""
    if not street and not city:
        return None, None, None, "not_found"
    # the suspected-wrong US state is dropped so it doesn't bias the lookup back into the US
    lat, lng, country_code = nominatim_international(street, city, "", "")
    time.sleep(NOMINATIM_DELAY)
    if lat and country_code and country_code != "US" and not _in_us_bounds(lat, lng):
        return lat, lng, country_code, "nominatim_intl"
    return None, None, None, "not_found"
