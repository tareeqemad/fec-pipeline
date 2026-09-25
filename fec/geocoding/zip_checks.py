"""Distances between a point and its filed ZIP's centroid and neighbours."""
import re
import statistics
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from fec.geocoding.places import distance_km as _distance_km
from fec.geocoding.places import valid_for_state as _valid_for_state

_ZIP_CENTROIDS = Path(__file__).resolve().parents[2] / "data" / "database" / "zip_centroids.csv"


# a city-level point and the filed ZIP farther apart than this contradict each other
_ZIP_OUTLIER_KM = 50


_STREET_ZIP_SPREAD = 2.5


_STREET_ZIP_FLOOR_KM = 5.0


_ZIP_NEIGHBOUR_RANK = 3


# a ZIP without a centroid is placed by this many nearest-numbered ZIPs of its 3-digit area
_ZIP_AREA_NEIGHBOURS = 4


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    if not _ZIP_CENTROIDS.exists():
        return {}
    rows = pd.read_csv(_ZIP_CENTROIDS, dtype={"zip": str})
    return {
        str(row.zip).zfill(5): (float(row.lat), float(row.lng))
        for row in rows.itertuples()
    }


def _far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    if not re.fullmatch(r"\d{5}", zipcode):
        return False
    centroid = _zip_centroids().get(zipcode)
    return bool(centroid and _distance_km((lat, lng), centroid) > _ZIP_OUTLIER_KM)


@lru_cache(maxsize=1)
def _centroid_radians() -> np.ndarray:
    centroids = _zip_centroids()
    if not centroids:
        return np.empty((0, 2))
    return np.radians(np.array(list(centroids.values()), dtype=float))


@lru_cache(maxsize=None)
def _zip_neighbour_km(zipcode: str) -> float | None:
    """Distance from the ZIP's centroid to its 3rd-nearest other ZIP centroid (the ZIP's size)."""
    centroid = _zip_centroids().get(zipcode)
    points = _centroid_radians()
    if centroid is None or len(points) <= _ZIP_NEIGHBOUR_RANK:
        return None
    lat, lng = np.radians(centroid)
    value = (np.sin((points[:, 0] - lat) / 2) ** 2
             + np.cos(lat) * np.cos(points[:, 0]) * np.sin((points[:, 1] - lng) / 2) ** 2)
    distances = 6371 * 2 * np.arcsin(np.sqrt(np.clip(value, 0, 1)))
    distances = np.sort(distances[distances > 0.01])  # drop the ZIP itself
    if len(distances) < _ZIP_NEIGHBOUR_RANK:
        return None
    return float(distances[_ZIP_NEIGHBOUR_RANK - 1])


def street_zip_limit_km(zipcode: str) -> float | None:
    """Farthest a street-level result may sit from the filed ZIP's centroid; None when the ZIP has no centroid."""
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    size = _zip_neighbour_km(zipcode)
    if size is None:
        return None
    return max(_STREET_ZIP_FLOOR_KM, _STREET_ZIP_SPREAD * size)


def _street_far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    """A street-level result outside its filed ZIP (threshold explained at STREET_LEVEL_SOURCES)."""
    centroid = _zip_centroids().get(zipcode) if re.fullmatch(r"\d{5}", zipcode or "") else None
    if centroid is None:
        return False
    distance = _distance_km((lat, lng), centroid)
    if distance <= _STREET_ZIP_FLOOR_KM:  # never beyond the limit; skips the neighbour search
        return False
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit


def _zip_point(zipcode: str, state: str) -> tuple[float, float] | None:
    """The filed ZIP's centroid, when it is a known 5-digit ZIP inside the filed state."""
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    point = _zip_centroids().get(zipcode)
    if point is None or not _valid_for_state(point[0], point[1], state):
        return None
    return point


def _zip_area_point(zipcode: str, state: str) -> tuple[float, float] | None:
    """Where the filed ZIP lies, to tell same-name towns apart: its centroid, or for a ZIP
    without one (PO-box-only and unique ZIPs: 94141, 78711, 20859) the median of the
    nearest-numbered ZIP centroids of its 3-digit area inside the filed state (20859's
    neighbours are Potomac and Rockville, not Potomac in Allegany County). None without a ZIP.
    """
    point = _zip_point(zipcode, state)
    if point is not None or not re.fullmatch(r"\d{5}", zipcode or ""):
        return point
    number = int(zipcode)
    neighbours = sorted(
        (abs(int(other) - number), lat, lng)
        for other, (lat, lng) in _zip_centroids().items()
        if other[:3] == zipcode[:3] and _valid_for_state(lat, lng, state)
    )[:_ZIP_AREA_NEIGHBOURS]
    if not neighbours:
        return None
    return (statistics.median(lat for _gap, lat, _lng in neighbours),
            statistics.median(lng for _gap, _lat, lng in neighbours))


def _zip_replaces_city(city_point: tuple[float, float], zip_point: tuple[float, float],
                       zipcode: str, po_box: bool) -> bool:
    """Whether the filed ZIP's centroid is a better approximate pin than the filed city's point.

    Never when the two contradict each other (over 50 km apart). A street address
    lies somewhere in its ZIP, so the ZIP's centroid wins. A PO box sits at the post
    office, in the named town, so the town's point stays unless it lies outside the
    filed ZIP altogether (downtown St. Louis for a Webster Groves 63119 box). A rural
    ZIP's centroid can be 20-30 km from its town (PO BOX 3530 SAN ANGELO 76902).
    """
    distance = _distance_km(city_point, zip_point)
    if distance > _ZIP_OUTLIER_KM:
        return False
    if not po_box:
        return True
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit
