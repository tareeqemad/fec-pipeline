"""Choose the closest known employer location."""
from __future__ import annotations

import csv
import math
from functools import lru_cache

from fec.cleaning.addresses import _normalize_street
from fec.cleaning.employer_synonyms import canonical_key
from fec.env import PROJECT_ROOT

ADDRESS_FIELDS = (
    "employer_address",
    "employer_city",
    "employer_state",
    "employer_zip",
)


def _text(value) -> str:
    return str(value or "").strip()


def _usable(location: dict | None) -> bool:
    return bool(location and (
        _text(location.get("employer_address"))
        or (
            location.get("method") == "manual_override"
            and _text(location.get("employer_city"))
            and _text(location.get("employer_state"))
        )
    ))


def _signature(location: dict) -> tuple[str, ...]:
    return tuple(_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


def _manual_signature(entry: dict) -> tuple[str, ...]:
    primary = location_candidates(entry)[0]
    street = _normalize_street(primary.get("employer_address"))
    return (
        _text(street).upper(),
        _text(primary.get("employer_city")).upper(),
        _text(primary.get("employer_state")).upper(),
        _text(primary.get("employer_zip")).upper(),
    )


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


def _preferred_entry(
    exact: dict | None,
    matches: list[dict],
) -> dict | None:
    if exact and exact.get("method") in {"manual_invalid", "ai_not_found"}:
        return exact
    invalid = [
        entry for entry in matches
        if entry.get("method") == "manual_invalid"
    ]
    if invalid:
        return invalid[0]

    manual = [
        entry for entry in matches
        if entry.get("method") == "manual_override"
        and location_candidates(entry)
    ]
    manual_addresses = {_manual_signature(entry) for entry in manual}
    if len(manual_addresses) == 1:
        return max(manual, key=lambda entry: len(location_candidates(entry)))

    if exact is not None:
        return exact

    usable = [entry for entry in matches if location_candidates(entry)]
    addresses = {
        _signature(location_candidates(entry)[0])
        for entry in usable
    }
    if len(addresses) == 1:
        return usable[0]

    return None


def address_cache_lookup(entries: dict) -> dict[str, dict]:
    """Index safe cache matches."""
    exact = {
        str(name).strip().upper(): entry
        for name, entry in entries.items()
    }
    grouped: dict[str, list[dict]] = {}
    for name, entry in entries.items():
        key = canonical_key(str(name))
        if key:
            grouped.setdefault(key, []).append(entry)

    lookup = {}
    for name, entry in exact.items():
        preferred = _preferred_entry(entry, grouped.get(canonical_key(name), []))
        if preferred is not None:
            lookup[name] = preferred

    for key, matches in grouped.items():
        preferred = _preferred_entry(None, matches)
        if preferred is not None:
            lookup[key] = preferred

    return lookup


def resolve_cache_entry(
    lookup: dict[str, dict],
    employer_name: str,
) -> dict | None:
    """Read one cache match."""
    exact = str(employer_name).strip().upper()
    return lookup.get(exact) or lookup.get(canonical_key(exact))


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    path = PROJECT_ROOT / "data" / "database" / "zip_centroids.csv"
    if not path.exists():
        return {}

    centroids = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                centroids[row["zip"]] = (float(row["lat"]), float(row["lng"]))
            except (KeyError, TypeError, ValueError):
                continue
    return centroids


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in miles."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 3958.8 * 2 * math.asin(min(1.0, math.sqrt(value)))


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
                ranked.append((_distance(donor_point, point), location))
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
