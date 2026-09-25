"""Which cached coordinates to trust, and when a ZIP centroid beats a city pin."""
from fec.geocoding.address_kind import is_foreign_address, is_po_box
from fec.geocoding.cache import GeoCache
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.reviewed_points import (
    is_reviewed_wrong,
    is_reviewed_zip_typo,
    reviewed_point,
)
from fec.geocoding.zip_checks import (
    _street_far_from_zip,
    _zip_point,
    _zip_replaces_city,
)

# Street-level results must sit inside the filed ZIP. A ZIP's size is measured as
# the distance from its centroid to the 3rd-nearest other ZIP centroid (the 1st and
# 2nd are often single-building or campus ZCTAs: 10112 Rockefeller Center, 08544
# Princeton University). On the 17,770 street-level results in the 2026-09-23 data
# the distance to the filed ZIP centroid is at most 1.98x that size for 99.5% of
# them; the 48 beyond 2.5x (0.27%) are same-named streets in another town (1 BRYANT
# PARK NY on Long Island, N LA SALLE ST Chicago on the South Side, 281 WILSHIRE AVE
# LA in Fullerton, '3 RD FLOOR' 103 km away) plus ZIP typos. Rural ZIPs (Kamas UT,
# Aspen, Casper) stay under 2.5x because their neighbours are far apart too. The
# 5 km floor keeps a dense downtown ZIP from rejecting a result a few blocks off.
# A result outside the filed ZIP is not wrong by that alone: when the same street
# cannot be found inside the filed ZIP and Census and Nominatim agree on the point,
# the ZIP is the typo and the point is kept if it lies in the filed city (see
# _resolve_outside_zip). Hand-checked typos are listed in reviewed_points.py.
STREET_LEVEL_SOURCES = frozenset({"census", "nominatim", "google"})


_REVIEWED_STREET_SOURCES = STREET_LEVEL_SOURCES | {"manual_census"}


# every REVIEWED_POINTS point is a copied Google street result
_REVIEWED_POINT_LEVEL = "google"


def accepted_coordinates(key: str, entry: dict | None) -> tuple[float | None, float | None, str]:
    """(lat, lng, level) a cached result may publish, or (None, None, reason) when the key's own address rules it out.

    A hand-checked point (reviewed_points.REVIEWED_POINTS) is published whatever the
    cache holds; a street point shown wrong (REVIEWED_WRONG_POINTS) never is."""
    reviewed = reviewed_point(key)
    if reviewed is not None:
        return reviewed[0], reviewed[1], _REVIEWED_POINT_LEVEL
    if not entry or entry.get("lat") is None:
        return None, None, "not_found"
    lat, lng = float(entry["lat"]), float(entry["lng"])
    source = entry.get("source") or ""
    country = entry.get("country") or "US"
    parts = key.split("|")
    if len(parts) != 4:
        return lat, lng, source
    _street, _city, state, zipcode = parts
    if is_foreign_address(state, zipcode):
        # a foreign office is published only with a foreign match (KIFO London sat in Connecticut)
        if country == "US":
            return None, None, "rejected_us_match_for_foreign"
        return lat, lng, source
    if country != "US":
        return lat, lng, source
    if not _valid_for_state(lat, lng, state):
        return None, None, "rejected_out_of_us"
    if _reviewed_wrong_street(key, entry, lat, lng):
        return None, None, "rejected_reviewed_wrong"
    if _unchecked_outside_zip(key, entry, lat, lng, zipcode):
        return None, None, "rejected_far_from_zip"
    return lat, lng, source


def _reviewed_wrong_street(key: str, entry: dict, lat: float, lng: float) -> bool:
    """A cached street-level point at the spot reviewed_points shows wrong for the key.

    A ZIP centroid or town point there is honest about its precision and stands."""
    return entry.get("source") in _REVIEWED_STREET_SOURCES and is_reviewed_wrong(key, lat, lng)


def _unchecked_outside_zip(key: str, entry: dict, lat: float, lng: float, zipcode: str) -> bool:
    """A cached street-level result outside its filed ZIP that no run and no reviewer has checked yet."""
    return (entry.get("source") in STREET_LEVEL_SOURCES
            and not entry.get("zip_checked")
            and not is_reviewed_zip_typo(key)
            and _street_far_from_zip(lat, lng, zipcode))


def prefer_zip_centroids(keys, cache: GeoCache) -> int:
    """Replace cached city-centroid fallbacks with the filed ZIP's centroid where _zip_replaces_city allows; returns entries changed."""
    changed = 0
    for key in keys:
        entry = cache.get(key)
        if (not entry or entry.get("source") != "nominatim_city"
                or entry.get("lat") is None or (entry.get("country") or "US") != "US"):
            continue
        parts = key.split("|")
        if len(parts) != 4 or is_foreign_address(parts[2], parts[3]):
            continue
        point = _zip_point(parts[3], parts[2])
        if point is None:
            continue
        city_point = (float(entry["lat"]), float(entry["lng"]))
        if not _zip_replaces_city(city_point, point, parts[3], is_po_box(parts[0])):
            continue
        cache.put(key, point[0], point[1], "zip_centroid", "US",
                  validated=True, zip_checked=True)
        changed += 1
    return changed
