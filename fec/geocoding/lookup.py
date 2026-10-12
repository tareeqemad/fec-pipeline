"""Geocode one address: street, ZIP, then city."""
import math
import time

from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS
from fec.geocoding.abroad import geocode_abroad_unless_us_zip, geocode_foreign
from fec.geocoding.address_kind import is_foreign_address, is_po_box
from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    CensusUnavailable,
    census,
    nominatim,
    nominatim_within,
)
from fec.geocoding.locality import Locality
from fec.geocoding.places import distance_km as _distance_km
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.reviewed_points import (
    REVIEWED_WRONG_POINTS,
    is_reviewed_wrong,
)
from fec.geocoding.street_text import clean_street_for_geocoding
from fec.geocoding.zip_checks import (
    ZIP_OUTLIER_KM,
    zip_replaces_city,
    street_zip_limit_km,
)
from fec.log import get_logger

logger = get_logger(__name__)

# two engines this close agree on place
_ENGINES_AGREE_KM = 1.0


# true if street result lies in ZIP
def _inside_filed_zip(lat: float, lng: float, locality: Locality) -> bool:
    if locality.zip_point is None:
        return True
    limit = street_zip_limit_km(locality.zipcode)
    return limit is None or _distance_km((lat, lng), locality.zip_point) <= limit


# accept in-ZIP, or in-city when ZIP contradicts
def _accept_street(lat: float, lng: float, locality: Locality) -> bool:
    if _inside_filed_zip(lat, lng, locality):
        return True
    city = locality.city_point() if locality.city else None
    if city is None:
        return False
    city_point = city[:2]
    return (_distance_km(city_point, locality.zip_point) > ZIP_OUTLIER_KM
            and _distance_km((lat, lng), city_point) <= ZIP_OUTLIER_KM)


# search box around the filed ZIP centroid
def _zip_search_box(locality: Locality) -> tuple[float, float, float, float] | None:
    limit = street_zip_limit_km(locality.zipcode) if locality.zip_point else None
    if limit is None:
        return None
    lat, lng = locality.zip_point
    dlat = limit / 111.0
    dlng = limit / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng


# search the same street within ZIP's box
def _street_in_filed_zip(street: str, locality: Locality) -> tuple | None:
    box = _zip_search_box(locality)
    if box is None or not street:
        return None
    lat, lng, country_code = nominatim_within(street, locality.city, locality.state, box)
    time.sleep(NOMINATIM_DELAY)
    if lat is None or not _valid_for_state(lat, lng, locality.state):
        return None
    if not _inside_filed_zip(lat, lng, locality):
        return None
    return lat, lng, country_code or "US", "nominatim"


# true if point lies near filed city
def _in_filed_city(lat: float, lng: float, locality: Locality) -> bool:
    city = locality.city_point() if locality.city else None
    anchor = city[:2] if city else locality.zip_point
    return anchor is not None and _distance_km((lat, lng), anchor) <= ZIP_OUTLIER_KM


# in-ZIP street wins; else engines must agree
def _resolve_outside_zip(street: str, locality: Locality, outside: list[tuple]) -> tuple | None:
    inside = _street_in_filed_zip(street, locality)
    if inside is not None:
        return inside
    if len(outside) >= 2:
        first, second = outside[0], outside[1]
        if (_distance_km(first[:2], second[:2]) <= _ENGINES_AGREE_KM
                and _in_filed_city(first[0], first[1], locality)):
            return first
    logger.debug("Street match outside ZIP %s not confirmed (%s, %s): left to the ZIP/city fallback",
                 locality.zipcode, street, locality.city)
    return None


# city or ZIP fallback when street fails
def _fallback(locality: Locality, require_zip_match: bool = False,
              po_box: bool = False) -> tuple | None:
    zip_point = locality.zip_point
    conflicting = None
    for zip_code in ([locality.zipcode, ""] if zip_point is not None else [""]):
        point = locality.city_point(zip_code)
        if point is None:
            continue
        lat, lng, country = point
        if zip_point is None:
            return lat, lng, country or "US", "nominatim_city"
        if _distance_km((lat, lng), zip_point) <= ZIP_OUTLIER_KM:
            if zip_replaces_city((lat, lng), zip_point, locality.zipcode, po_box):
                return zip_point[0], zip_point[1], "US", "zip_centroid"
            return lat, lng, country or "US", "nominatim_city"  # a PO box in its own town
        conflicting = conflicting or point
    if zip_point is not None and conflicting is None:
        # city unknown to Nominatim; only ZIP remains
        return zip_point[0], zip_point[1], "US", "zip_centroid"
    if conflicting is not None and not require_zip_match:
        lat, lng, country = conflicting
        return lat, lng, country or "US", "nominatim_city"
    return None


# city fallback as a (lat, lng, country)
def _city_fallback(city: str, state: str, zipcode: str,
                   require_zip_match: bool = False) -> tuple:
    result = _fallback(Locality(city, state, zipcode), require_zip_match)
    if result is None:
        return None, None, None
    return result[:3]


# geocode one address via street, ZIP, city
def geocode_one(street, city, state, zipcode):
    if is_foreign_address(state, zipcode):
        return geocode_foreign(street, city, state, zipcode)

    if not city and not state:
        return None, None, None, "not_found"

    locality = Locality(city, state, zipcode)
    raw_street = street
    if is_po_box(street):
        result = _fallback(locality, po_box=True)
        if result:
            return result
        return geocode_abroad_unless_us_zip(street, city, locality)

    if street:
        street = clean_street_for_geocoding(street)

    if f"{raw_street}|{city}|{state}|{zipcode}" in REVIEWED_WRONG_POINTS:
        return _geocode_reviewed_wrong(raw_street, street, locality)

    outside = []  # street results outside the filed ZIP
    if street and state in _STATE_BOUNDS:
        try:
            lat, lng, country_code = census(street, city, state, zipcode)
        except CensusUnavailable as error:
            logger.debug("Census Geocoder unavailable for %s: %s", street, error)
        else:
            if lat and _valid_for_state(lat, lng, state):
                if _accept_street(lat, lng, locality):
                    return lat, lng, country_code or "US", "census"
                outside.append((lat, lng, country_code or "US", "census"))

    if street:
        lat, lng, country_code = nominatim(street, city, state, zipcode)
        time.sleep(NOMINATIM_DELAY)
        if lat and _valid_for_state(lat, lng, state):
            if _accept_street(lat, lng, locality):
                return lat, lng, country_code or "US", "nominatim"
            logger.debug("Nominatim result lat=%.4f lng=%.4f outside ZIP %s", lat, lng, zipcode)
            outside.append((lat, lng, country_code or "US", "nominatim"))
        elif lat:
            logger.debug("Nominatim result lat=%.4f lng=%.4f rejected - outside %s", lat, lng, state)

    if outside:
        result = _resolve_outside_zip(street, locality, outside)
        if result:
            return result

    result = _fallback(locality, require_zip_match=True)
    if result:
        return result

    # mislabeled foreign address is kept, not dropped
    return geocode_abroad_unless_us_zip(street, city, locality)


# re-geocode a reviewed-wrong key via Census only
def _geocode_reviewed_wrong(key_street: str, street: str, locality: Locality) -> tuple:
    key = f"{key_street}|{locality.city}|{locality.state}|{locality.zipcode}"
    if street and locality.state in _STATE_BOUNDS:
        lat, lng, country_code = census(street, locality.city, locality.state, locality.zipcode)
        if (lat is not None and _valid_for_state(lat, lng, locality.state)
                and _inside_filed_zip(lat, lng, locality) and not is_reviewed_wrong(key, lat, lng)):
            return lat, lng, country_code or "US", "census"
    if locality.zip_point is not None:
        return locality.zip_point[0], locality.zip_point[1], "US", "zip_centroid"
    result = _fallback(locality, require_zip_match=True)
    return result or (None, None, None, "not_found")


