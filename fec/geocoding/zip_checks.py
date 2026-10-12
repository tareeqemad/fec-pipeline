"""Distance checks against a filed ZIP's centroid."""
import re
import statistics
from functools import lru_cache

import numpy as np
import pandas as pd

from fec.env import RULES_DIR
from fec.geocoding.places import distance_km as _distance_km
from fec.geocoding.places import valid_for_state as _valid_for_state

_ZIP_CENTROIDS = RULES_DIR / "zip_centroids.csv"


# city and ZIP farther apart contradict
ZIP_OUTLIER_KM = 50


_STREET_ZIP_SPREAD = 2.5


_STREET_ZIP_FLOOR_KM = 5.0


_ZIP_NEIGHBOUR_RANK = 3


# nearest-numbered ZIPs used to place centroidless ZIPs
_ZIP_AREA_NEIGHBOURS = 4


# load and cache ZIP centroids from CSV
@lru_cache(maxsize=1)
def load_zip_centroids() -> dict[str, tuple[float, float]]:
    if not _ZIP_CENTROIDS.exists():
        return {}
    rows = pd.read_csv(_ZIP_CENTROIDS, dtype={"zip": str})
    return {
        str(row.zip).zfill(5): (float(row.lat), float(row.lng))
        for row in rows.itertuples()
    }


# true if far from the ZIP centroid
def point_far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    if not re.fullmatch(r"\d{5}", zipcode):
        return False
    centroid = load_zip_centroids().get(zipcode)
    return bool(centroid and _distance_km((lat, lng), centroid) > ZIP_OUTLIER_KM)


# all ZIP centroids as a radians array
@lru_cache(maxsize=1)
def _centroid_radians() -> np.ndarray:
    centroids = load_zip_centroids()
    if not centroids:
        return np.empty((0, 2))
    return np.radians(np.array(list(centroids.values()), dtype=float))


# ZIP size: distance to 3rd-nearest other centroid
@lru_cache(maxsize=None)
def _zip_neighbour_km(zipcode: str) -> float | None:
    centroid = load_zip_centroids().get(zipcode)
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


# max street-result distance from ZIP centroid
def street_zip_limit_km(zipcode: str) -> float | None:
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    size = _zip_neighbour_km(zipcode)
    if size is None:
        return None
    return max(_STREET_ZIP_FLOOR_KM, _STREET_ZIP_SPREAD * size)


# true if street result is outside ZIP
def street_far_from_zip(lat: float, lng: float, zipcode: str) -> bool:
    centroid = load_zip_centroids().get(zipcode) if re.fullmatch(r"\d{5}", zipcode or "") else None
    if centroid is None:
        return False
    distance = _distance_km((lat, lng), centroid)
    if distance <= _STREET_ZIP_FLOOR_KM:  # within floor; skips the neighbour search
        return False
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit


# ZIP centroid, if known and in state
def zip_center_point(zipcode: str, state: str) -> tuple[float, float] | None:
    if not re.fullmatch(r"\d{5}", zipcode or ""):
        return None
    point = load_zip_centroids().get(zipcode)
    if point is None or not _valid_for_state(point[0], point[1], state):
        return None
    return point


# ZIP location: centroid or median of neighbours
def zip_area_point(zipcode: str, state: str) -> tuple[float, float] | None:
    point = zip_center_point(zipcode, state)
    if point is not None or not re.fullmatch(r"\d{5}", zipcode or ""):
        return point
    number = int(zipcode)
    neighbours = sorted(
        (abs(int(other) - number), lat, lng)
        for other, (lat, lng) in load_zip_centroids().items()
        if other[:3] == zipcode[:3] and _valid_for_state(lat, lng, state)
    )[:_ZIP_AREA_NEIGHBOURS]
    if not neighbours:
        return None
    return (statistics.median(lat for _gap, lat, _lng in neighbours),
            statistics.median(lng for _gap, _lat, lng in neighbours))


# true if ZIP centroid beats city pin
def zip_replaces_city(city_point: tuple[float, float], zip_point: tuple[float, float],
                       zipcode: str, po_box: bool) -> bool:
    distance = _distance_km(city_point, zip_point)
    if distance > ZIP_OUTLIER_KM:
        return False
    if not po_box:
        return True
    # PO box keeps town unless outside ZIP
    limit = street_zip_limit_km(zipcode)
    return limit is not None and distance > limit
