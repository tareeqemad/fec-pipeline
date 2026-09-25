"""Geocode one address: street in its filed ZIP first, then ZIP, then city."""
import math
import time

from fec.config.geography import US_STATE_BBOX as _STATE_BOUNDS
from fec.geocoding.address_kind import is_foreign_address, is_po_box
from fec.geocoding.engines import (
    NOMINATIM_DELAY,
    CensusUnavailable,
    census,
    city_level,
    nominatim,
    nominatim_international,
    nominatim_within,
)
from fec.geocoding.places import distance_km as _distance_km
from fec.geocoding.places import in_us_bounds as _in_us_bounds
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.reviewed_points import (
    REVIEWED_WRONG_POINTS,
    is_reviewed_wrong,
)
from fec.geocoding.street_text import _clean_street_for_geocoding
from fec.geocoding.zip_checks import (
    _ZIP_OUTLIER_KM,
    _zip_area_point,
    _zip_point,
    _zip_replaces_city,
    street_zip_limit_km,
)
from fec.log import get_logger

logger = get_logger(__name__)

# two engines' points for one address this close together are the same place
# (Census address ranges vs OSM: 0.01-0.7 km apart for the 2026-09-23 typos)
_ENGINES_AGREE_KM = 1.0


class _Locality:
    """The filed city/state/ZIP of one address; each city-level query runs at most once."""

    # store the filed city/state/ZIP and its zip-point lookups
    def __init__(self, city: str, state: str, zipcode: str):
        self.city = city
        self.state = state
        self.zipcode = zipcode
        self.zip_point = _zip_point(zipcode, state)
        self.zip_area = self.zip_point or _zip_area_point(zipcode, state)
        self._city_points: dict[str, tuple | None] = {}

    # look up and cache the filed town's own point
    def city_point(self, zipcode: str = "") -> tuple | None:
        """(lat, lng, country) of the filed town itself (a settlement of that name, inside the filed state; see engines.city_level), optionally searched with the filed ZIP."""
        if zipcode not in self._city_points:
            point = None
            if self.city:
                lat, lng, country = city_level(self.city, self.state, zipcode, self.zip_area)
                time.sleep(NOMINATIM_DELAY)
                if lat is not None and _valid_for_state(lat, lng, self.state):
                    point = (lat, lng, country)
            self._city_points[zipcode] = point
        return self._city_points[zipcode]


# check whether a street-level result lies inside the filed ZIP
def _inside_filed_zip(lat: float, lng: float, locality: _Locality) -> bool:
    """A street-level result inside the filed ZIP (always true when the ZIP has no centroid to test against)."""
    if locality.zip_point is None:
        return True
    limit = street_zip_limit_km(locality.zipcode)
    return limit is None or _distance_km((lat, lng), locality.zip_point) <= limit


# decide whether to accept a street-level geocoding result
def _accept_street(lat: float, lng: float, locality: _Locality) -> bool:
    """Keep a street-level result inside the filed ZIP, or outside it when the filed ZIP contradicts the filed city (a ZIP typo 50+ km off) and the result lies in that city."""
    if _inside_filed_zip(lat, lng, locality):
        return True
    city = locality.city_point() if locality.city else None
    if city is None:
        return False
    city_point = city[:2]
    return (_distance_km(city_point, locality.zip_point) > _ZIP_OUTLIER_KM
            and _distance_km((lat, lng), city_point) <= _ZIP_OUTLIER_KM)


# compute a bounding box around the filed ZIP's centroid
def _zip_search_box(locality: _Locality) -> tuple[float, float, float, float] | None:
    """(south, west, north, east) around the filed ZIP's centroid, as wide as the street-inside-ZIP limit."""
    limit = street_zip_limit_km(locality.zipcode) if locality.zip_point else None
    if limit is None:
        return None
    lat, lng = locality.zip_point
    dlat = limit / 111.0
    dlng = limit / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - dlat, lng - dlng, lat + dlat, lng + dlng


# search for the same street restricted to the ZIP's box
def _street_in_filed_zip(street: str, locality: _Locality) -> tuple | None:
    """The same street in the filed city, searched only around the filed ZIP; a hit inside the ZIP, or None when the ZIP has no such street.

    The city is part of the query: without it a same-named street in the next town
    at the edge of the search circle counts (474 CENTRAL AVE PASSAIC matched in
    Hackensack)."""
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


# check whether a point is near the filed city
def _in_filed_city(lat: float, lng: float, locality: _Locality) -> bool:
    """A point within 50 km of the filed city's point; of the filed ZIP's centroid when the city is missing or unknown."""
    city = locality.city_point() if locality.city else None
    anchor = city[:2] if city else locality.zip_point
    return anchor is not None and _distance_km((lat, lng), anchor) <= _ZIP_OUTLIER_KM


# decide whether an out-of-ZIP street match should still be trusted
def _resolve_outside_zip(street: str, locality: _Locality, outside: list[tuple]) -> tuple | None:
    """Street results that landed outside the filed ZIP (engine order: census, nominatim).

    If the street exists inside the filed ZIP, the outside result was a same-named
    street in another town (10 S LA SALLE ST CHICAGO 60603 matched on the South
    Side): the hit inside the ZIP wins. If the filed ZIP has no such street, the ZIP
    may be the typo (2160 GOLD ST SAN JOSE filed with 95112 is in Alviso, 95002).
    The outside point is kept only when Census and Nominatim independently put the
    address at the same spot, in the filed city. One engine alone proves nothing:
    Nominatim put 1 BRYANT PARK in Yonkers and Census put 1 FOUNTAIN SQUARE
    CHATTANOOGA in East Ridge, while neither street could be found inside its ZIP.
    Otherwise None, and the caller falls back to the filed ZIP.
    """
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


# fall back to city or ZIP when street geocoding fails
def _fallback(locality: _Locality, require_zip_match: bool = False,
              po_box: bool = False) -> tuple | None:
    """City-level fallback: the filed ZIP's centroid when _zip_replaces_city allows, else the town's point; None on a city/ZIP conflict when require_zip_match.

    The ZIP is part of the town search only when it has a centroid: OSM does not know
    PO-box-only and unique ZIPs (94141, 78711). With no centroid to test against, the
    town's own settlement point is the pin (a PO box sits at the post office, in the town).
    """
    zip_point = locality.zip_point
    conflicting = None
    for zip_code in ([locality.zipcode, ""] if zip_point is not None else [""]):
        point = locality.city_point(zip_code)
        if point is None:
            continue
        lat, lng, country = point
        if zip_point is None:
            return lat, lng, country or "US", "nominatim_city"
        if _distance_km((lat, lng), zip_point) <= _ZIP_OUTLIER_KM:
            if _zip_replaces_city((lat, lng), zip_point, locality.zipcode, po_box):
                return zip_point[0], zip_point[1], "US", "zip_centroid"
            return lat, lng, country or "US", "nominatim_city"  # a PO box in its own town
        conflicting = conflicting or point
    if zip_point is not None and conflicting is None:
        # the city is unknown to Nominatim: the filed ZIP is the only evidence left
        return zip_point[0], zip_point[1], "US", "zip_centroid"
    if conflicting is not None and not require_zip_match:
        lat, lng, country = conflicting
        return lat, lng, country or "US", "nominatim_city"
    return None


# wrap _fallback to return a plain (lat, lng, country) tuple
def _city_fallback(city: str, state: str, zipcode: str,
                   require_zip_match: bool = False) -> tuple:
    """(lat, lng, country) of the city-level fallback, or Nones."""
    result = _fallback(_Locality(city, state, zipcode), require_zip_match)
    if result is None:
        return None, None, None
    return result[:3]


# geocode one address through street, ZIP and city fallbacks
def _geocode_one(street, city, state, zipcode):
    """Geocode one address; a foreign address only ever uses the international engine."""
    if is_foreign_address(state, zipcode):
        return _geocode_foreign(street, city, state, zipcode)

    if not city and not state:
        return None, None, None, "not_found"

    locality = _Locality(city, state, zipcode)
    raw_street = street
    if is_po_box(street):
        result = _fallback(locality, po_box=True)
        if result:
            return result
        return _geocode_abroad_unless_us_zip(street, city, locality)

    if street:
        street = _clean_street_for_geocoding(street)

    if f"{raw_street}|{city}|{state}|{zipcode}" in REVIEWED_WRONG_POINTS:
        return _geocode_reviewed_wrong(raw_street, street, locality)

    outside = []  # street-level results that landed outside the filed ZIP
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

    # a foreign address mislabeled with a US state is kept and flagged, not dropped
    return _geocode_abroad_unless_us_zip(street, city, locality)


# re-geocode a key whose cached point was flagged wrong
def _geocode_reviewed_wrong(key_street: str, street: str, locality: _Locality) -> tuple:
    """A key whose cached street point was shown wrong: Census inside the filed ZIP, else the ZIP's centroid.

    Nominatim is skipped: it gave most of these points (the same-name street inside
    the city of the postal name) and would give them again. A Census answer at the
    rejected spot is refused. CensusUnavailable is raised, so an outage is retried
    next run instead of settling for the centroid."""
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
