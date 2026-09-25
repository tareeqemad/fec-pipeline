"""Pick one location per employer: nearest to its donors, else a ZIP centroid."""
from __future__ import annotations

import csv
from functools import lru_cache

from fec.env import PROJECT_ROOT
from fec.geocoding.places import EARTH_RADIUS_MILES, great_circle
from fec.resolve.pipeline.locations import _signature, _text, location_candidates


# load zip -> (lat, lng) centroids from csv, cached
@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    path = PROJECT_ROOT / "data" / "database" / "zip_centroids.csv"
    if not path.exists():
        return {}

    centroids = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                centroids[str(row["zip"]).strip().zfill(5)] = (float(row["lat"]), float(row["lng"]))
            except (KeyError, TypeError, ValueError):
                continue
    return centroids


# centroid coordinates for a 5-digit zip, if known
def zip_centroid(zipcode: str) -> tuple[float, float] | None:
    """The 5-digit ZIP's centroid, or None when it has none."""
    return _zip_centroids().get(_text(zipcode)[:5])


# pick the best employer location: same state, then nearest
def select_location(
    entry: dict | None,
    donor_zip: str = "",
    donor_state: str = "",
) -> dict | None:
    """Prefer a same-state office, then the primary location."""
    candidates = location_candidates(entry)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    donor_zip = _text(donor_zip)[:5]
    donor_state = _text(donor_state).upper()
    same_state = [
        location for location in candidates
        if _text(location.get("employer_state")).upper() == donor_state
    ]

    if not same_state:
        return next(
            (location for location in candidates if location.get("is_primary")),
            candidates[0],
        )

    centroids = _zip_centroids()
    donor_point = centroids.get(donor_zip)

    if donor_point:
        ranked = []
        for location in same_state:
            point = centroids.get(_text(location.get("employer_zip"))[:5])
            if point:
                ranked.append((great_circle(donor_point, point, EARTH_RADIUS_MILES), location))
        if ranked:
            return min(
                ranked,
                key=lambda item: (
                    item[0],
                    not item[1].get("is_primary", False),
                    _signature(item[1]),
                ),
            )[1]

    return min(
        same_state,
        key=lambda location: (
            not location.get("is_primary", False),
            _signature(location),
        ),
    )
