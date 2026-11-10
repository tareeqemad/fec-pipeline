"""Choose the company office for each employment: same city, else the nearest in the state."""

from __future__ import annotations

import csv
from functools import lru_cache
from typing import Any

import pandas as pd

from fec.common.settings import REFERENCE_DIR
from fec.database.addresses import _akey, _employer_locations
from fec.database.connection import to_native
from fec.geocoding.coordinate_checks import EARTH_RADIUS_MILES, great_circle


def _location_index(
    employer_locations: list[dict] | None = None,
) -> dict[str, dict]:
    """Group offices by company, keeping the primary office first."""
    grouped: dict[str, list[dict]] = {}
    for location in _employer_locations(employer_locations):
        grouped.setdefault(location["employer_name"], []).append(location)

    lookup = {}
    for name, locations in grouped.items():
        primary = next(
            (location for location in locations if location["is_primary"]),
            None,
        )
        lookup[name] = {**(primary or {}), "locations": [location for location in locations if location is not primary]}
    return lookup


def _employment_address_id(
    row: pd.Series,
    employer_name: Any,
    locations: dict,
    address_ids: dict,
) -> int | None:
    """Choose a company office or the self-employed donor address."""
    status = str(to_native(row.get("employer_status")) or "")

    if status == "not_employed":
        return None

    if status == "self_employed":
        address = _akey(
            row.get("contributor_street_1"),
            row.get("contributor_street_2"),
            row.get("contributor_city"),
            row.get("contributor_state"),
            row.get("contributor_zip"),
        )
        if not any(address):
            return None
        address_id = address_ids.get(address)
        if address_id is None:
            raise RuntimeError(f"self-employed address was not loaded: donor_key={row.get('donor_key')}")
        return address_id

    name = str(to_native(employer_name) or "").strip()
    if not name or not locations:
        return None

    entry = locations.get(name)
    location = select_location(
        entry,
        str(to_native(row.get("contributor_zip")) or ""),
        str(to_native(row.get("contributor_state")) or ""),
        str(to_native(row.get("contributor_city")) or ""),
    )
    if not location:
        return None

    address = _akey(
        location["employer_address"],
        None,
        location["employer_city"],
        location["employer_state"],
        location["employer_zip"],
    )
    address_id = address_ids.get(address)
    if address_id is None:
        raise RuntimeError(f"employer address was not loaded: {name!r}")
    return address_id


ADDRESS_FIELDS = (
    "employer_address",
    "employer_city",
    "employer_state",
    "employer_zip",
)


def _text(value: Any) -> str:
    """Return the value as trimmed text, '' when missing."""
    return str(value or "").strip()


def _usable(location: dict | None) -> bool:
    """Check that a location has a street, or a manual city and state."""
    return bool(
        location
        and (
            _text(location.get("employer_address"))
            or (
                location.get("method") == "manual_override"
                and _text(location.get("employer_city"))
                and _text(location.get("employer_state"))
            )
        )
    )


def _signature(location: dict) -> tuple[str, ...]:
    """Return the upper-cased address fields that identify a location."""
    return tuple(_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


def location_candidates(entry: dict | None) -> list[dict]:
    """Return unique locations from one employer cache entry."""
    if not isinstance(entry, dict):
        return []

    candidates = []
    if _usable(entry):
        candidates.append({**entry, "is_primary": True})
    for location in entry.get("locations", []):
        if isinstance(location, dict) and _usable(location):
            candidates.append({**location, "is_primary": False})

    unique = {}
    for location in candidates:
        unique.setdefault(_signature(location), location)
    return list(unique.values())


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    """Load ZIP code centroids from the reference CSV, empty when missing."""
    path = REFERENCE_DIR / "zip_centroids.csv"
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


def select_location(
    entry: dict | None,
    donor_zip: str = "",
    donor_state: str = "",
    donor_city: str = "",
) -> dict | None:
    """Choose the office in the same city and state, else the nearest one in the state."""
    candidates = location_candidates(entry)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    donor_zip = _text(donor_zip)[:5]
    donor_state = _text(donor_state).upper()
    same_state = [location for location in candidates if _text(location.get("employer_state")).upper() == donor_state]

    if not same_state:
        return next(
            (location for location in candidates if location.get("is_primary")),
            candidates[0],
        )

    donor_city = _text(donor_city).upper()
    same_city = [
        location for location in same_state if donor_city and _text(location.get("employer_city")).upper() == donor_city
    ]
    same_state = same_city or same_state
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
