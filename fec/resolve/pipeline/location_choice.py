"""Pick an employer location near the donor."""
from __future__ import annotations

from fec.geocoding.places import EARTH_RADIUS_MILES, great_circle
from fec.geocoding.zip_checks import load_zip_centroids
from fec.resolve.pipeline.locations import location_signature, stripped_text, location_candidates


# centroid of a 5-digit zip, if known
def zip_centroid(zipcode: str) -> tuple[float, float] | None:
    return load_zip_centroids().get(stripped_text(zipcode)[:5])


# best location: same state, then nearest
def select_location(
    entry: dict | None,
    donor_zip: str = "",
    donor_state: str = "",
) -> dict | None:
    candidates = location_candidates(entry)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    donor_zip = stripped_text(donor_zip)[:5]
    donor_state = stripped_text(donor_state).upper()
    same_state = [
        location for location in candidates
        if stripped_text(location.get("employer_state")).upper() == donor_state
    ]

    if not same_state:
        return next(
            (location for location in candidates if location.get("is_primary")),
            candidates[0],
        )

    centroids = load_zip_centroids()
    donor_point = centroids.get(donor_zip)

    if donor_point:
        ranked = []
        for location in same_state:
            point = centroids.get(stripped_text(location.get("employer_zip"))[:5])
            if point:
                ranked.append((great_circle(donor_point, point, EARTH_RADIUS_MILES), location))
        if ranked:
            return min(
                ranked,
                key=lambda item: (
                    item[0],
                    not item[1].get("is_primary", False),
                    location_signature(item[1]),
                ),
            )[1]

    return min(
        same_state,
        key=lambda location: (
            not location.get("is_primary", False),
            location_signature(location),
        ),
    )
