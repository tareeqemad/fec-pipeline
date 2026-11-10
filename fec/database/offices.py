"""Choose the company office for each employment: same city, else the nearest in the state."""

from __future__ import annotations

import csv
from functools import lru_cache
from typing import Any

import pandas as pd

from fec.common.settings import REFERENCE_DIR
from fec.database.addresses import address_key, employer_offices_or_file
from fec.database.connection import to_native
from fec.geocoding.coordinate_checks import EARTH_RADIUS_MILES, great_circle


def offices_by_company(
    employer_locations: list[dict] | None = None,
) -> dict[str, dict]:
    """Group offices by company, keeping the primary office first."""
    grouped: dict[str, list[dict]] = {}
    for location in employer_offices_or_file(employer_locations):
        grouped.setdefault(location["employer_name"], []).append(location)

    lookup = {}
    for name, locations in grouped.items():
        primary = next(
            (location for location in locations if location["is_primary"]),
            None,
        )
        lookup[name] = {
            **(primary or {}),
            "locations": [location for location in locations if location is not primary],
        }
    return lookup


def filing_office_address_id(
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
        return _find_self_employed_address_id(row, address_ids)
    return _find_company_office_address_id(row, employer_name, locations, address_ids)


def _find_self_employed_address_id(row: pd.Series, address_ids: dict) -> int | None:
    """Return the donor's own address ID, None when the filing has no address."""
    address = address_key(
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


def _find_company_office_address_id(
    row: pd.Series,
    employer_name: Any,
    locations: dict,
    address_ids: dict,
) -> int | None:
    """Return the address ID of the company office chosen for this donor."""
    name = str(to_native(employer_name) or "").strip()
    if not name or not locations:
        return None

    company_offices = locations.get(name)
    location = select_office(
        company_offices,
        str(to_native(row.get("contributor_zip")) or ""),
        str(to_native(row.get("contributor_state")) or ""),
        str(to_native(row.get("contributor_city")) or ""),
    )
    if not location:
        return None

    address = address_key(
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


def _trim_text(value: Any) -> str:
    """Return the value as trimmed text, '' when missing."""
    return str(value or "").strip()


def _is_usable(location: dict | None) -> bool:
    """Check that a location has a street, or a manual city and state."""
    if not location:
        return False
    if _trim_text(location.get("employer_address")):
        return True
    return bool(
        location.get("method") == "manual_override"
        and _trim_text(location.get("employer_city"))
        and _trim_text(location.get("employer_state"))
    )


def _build_signature(location: dict) -> tuple[str, ...]:
    """Return the upper-cased address fields that identify a location."""
    return tuple(_trim_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


def office_candidates(entry: dict | None) -> list[dict]:
    """Return unique locations from one employer cache entry."""
    if not isinstance(entry, dict):
        return []

    candidates = []
    if _is_usable(entry):
        candidates.append({**entry, "is_primary": True})
    other_offices = [
        {**location, "is_primary": False}
        for location in entry.get("locations", [])
        if isinstance(location, dict) and _is_usable(location)
    ]
    candidates.extend(other_offices)

    unique = {}
    for location in candidates:
        unique.setdefault(_build_signature(location), location)
    return list(unique.values())


@lru_cache(maxsize=1)
def _read_zip_centroids() -> dict[str, tuple[float, float]]:
    """Load ZIP code centroids from the reference CSV, empty when missing."""
    path = REFERENCE_DIR / "zip_centroids.csv"
    if not path.exists():
        return {}

    centroids = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                point = (float(row["lat"]), float(row["lng"]))
                zip_code = str(row["zip"]).strip().zfill(5)
            except (KeyError, TypeError, ValueError):
                continue
            centroids[zip_code] = point
    return centroids


def select_office(
    entry: dict | None,
    donor_zip: str = "",
    donor_state: str = "",
    donor_city: str = "",
) -> dict | None:
    """Choose the office in the same city and state, else the nearest one in the state."""
    candidates = office_candidates(entry)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    donor_zip = _trim_text(donor_zip)[:5]
    donor_state = _trim_text(donor_state).upper()
    same_state = [
        location for location in candidates if _trim_text(location.get("employer_state")).upper() == donor_state
    ]

    if not same_state:
        return next(
            (location for location in candidates if location.get("is_primary")),
            candidates[0],
        )

    donor_city = _trim_text(donor_city).upper()
    same_city = [
        location
        for location in same_state
        if donor_city and _trim_text(location.get("employer_city")).upper() == donor_city
    ]
    nearby = same_city or same_state

    nearest = _find_nearest_office(nearby, donor_zip)
    if nearest is not None:
        return nearest
    return min(nearby, key=_rank_office)


def _find_nearest_office(offices: list[dict], donor_zip: str) -> dict | None:
    """Return the office whose ZIP centroid is closest to the donor's, None when unknown."""
    centroids = _read_zip_centroids()
    donor_point = centroids.get(donor_zip)
    if not donor_point:
        return None

    ranked = []
    for location in offices:
        point = centroids.get(_trim_text(location.get("employer_zip"))[:5])
        if point:
            ranked.append((great_circle(donor_point, point, EARTH_RADIUS_MILES), location))
    if not ranked:
        return None
    return min(ranked, key=_rank_by_distance)[1]


def _rank_office(location: dict) -> tuple[bool, tuple[str, ...]]:
    """Rank the primary office first, then by address."""
    return (not location.get("is_primary", False), _build_signature(location))


def _rank_by_distance(ranked_office: tuple[float, dict]) -> tuple[float, bool, tuple[str, ...]]:
    """Rank by distance, then the primary office first, then by address."""
    distance, location = ranked_office
    return (distance, not location.get("is_primary", False), _build_signature(location))
