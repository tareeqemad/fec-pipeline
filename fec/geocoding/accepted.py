"""Trusted cached coordinates and ZIP centroid preference."""
from fec.geocoding.address_kind import is_foreign_address, is_po_box
from fec.geocoding.cache import GeoCache
from fec.geocoding.places import valid_for_state as _valid_for_state
from fec.geocoding.reviewed_points import (
    is_reviewed_wrong,
    is_reviewed_zip_typo,
    reviewed_point,
)
from fec.geocoding.zip_checks import (
    street_far_from_zip,
    zip_center_point,
    zip_replaces_city,
)

# street-level results must sit inside filed ZIP
STREET_LEVEL_SOURCES = frozenset({"census", "nominatim", "google"})


_REVIEWED_STREET_SOURCES = STREET_LEVEL_SOURCES | {"manual_census"}


# reviewed points are copied Google street results
_REVIEWED_POINT_LEVEL = "google"


# which cached coordinates a key may publish
def accepted_coordinates(key: str, entry: dict | None) -> tuple[float | None, float | None, str]:
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
        # foreign address needs a foreign match
        if country == "US":
            return None, None, "rejected_us_match_for_foreign"
        return lat, lng, source
    if country != "US":
        return lat, lng, source
    if not _valid_for_state(lat, lng, state):
        return None, None, "rejected_out_of_us"
    if reviewed_wrong_street(key, entry, lat, lng):
        return None, None, "rejected_reviewed_wrong"
    if unchecked_outside_zip(key, entry, lat, lng, zipcode):
        return None, None, "rejected_far_from_zip"
    return lat, lng, source


# true for a reviewed-wrong cached street point
def reviewed_wrong_street(key: str, entry: dict, lat: float, lng: float) -> bool:
    return entry.get("source") in _REVIEWED_STREET_SOURCES and is_reviewed_wrong(key, lat, lng)


# true for unchecked street result outside ZIP
def unchecked_outside_zip(key: str, entry: dict, lat: float, lng: float, zipcode: str) -> bool:
    return (entry.get("source") in STREET_LEVEL_SOURCES
            and not entry.get("zip_checked")
            and not is_reviewed_zip_typo(key)
            and street_far_from_zip(lat, lng, zipcode))


# swap cached city fallbacks for ZIP centroids
def prefer_zip_centroids(keys, cache: GeoCache) -> int:
    changed = 0
    for key in keys:
        entry = cache.get(key)
        if (not entry or entry.get("source") != "nominatim_city"
                or entry.get("lat") is None or (entry.get("country") or "US") != "US"):
            continue
        parts = key.split("|")
        if len(parts) != 4 or is_foreign_address(parts[2], parts[3]):
            continue
        point = zip_center_point(parts[3], parts[2])
        if point is None:
            continue
        city_point = (float(entry["lat"]), float(entry["lng"]))
        if not zip_replaces_city(city_point, point, parts[3], is_po_box(parts[0])):
            continue
        cache.put(key, point[0], point[1], "zip_centroid", "US",
                  validated=True, zip_checked=True)
        changed += 1
    return changed
