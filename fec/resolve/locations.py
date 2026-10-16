"""Choose cache entries and offices for employer addresses."""
from __future__ import annotations

import csv
from functools import lru_cache

from fec.cleaning.addresses.street_text import normalize_street
from fec.cleaning.work.employer_canonical import canonical_key
from fec.env import RULES_DIR
from fec.geocoding.validation import EARTH_RADIUS_MILES, great_circle


ADDRESS_FIELDS = (
    "employer_address",
    "employer_city",
    "employer_state",
    "employer_zip",
)
ADDRESS_TRUST_VALUES = frozenset({
    "verified",
    "grounded",
    "corroborated",
    "uncorroborated",
})
PUBLISHABLE_ADDRESS_TRUST = frozenset({"verified", "grounded"})
_CACHE_BLOCK_METHODS = frozenset({
    "ai_not_found",
    "manual_invalid",
    "manual_review",
})


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


def is_publishable_location(location: dict | None) -> bool:
    """Return whether an address has auditable evidence."""
    if not _usable(location):
        return False
    method = _text(location.get("method"))
    return method == "manual_override" or method.endswith("_search")


def _signature(location: dict) -> tuple[str, ...]:
    return tuple(_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


def _manual_signature(entry: dict) -> tuple[str, ...]:
    primary = location_candidates(entry)[0]
    street = normalize_street(primary.get("employer_address"))
    return _signature({**primary, "employer_address": street})


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


def _publishable_entry(entry: dict | None) -> dict | None:
    """Keep blockers or return only publishable locations."""
    if not isinstance(entry, dict):
        return None
    if entry.get("method") in _CACHE_BLOCK_METHODS:
        return entry

    candidates = [
        location
        for location in location_candidates(entry)
        if is_publishable_location(location)
    ]
    if not candidates:
        return None

    primary = next(
        (location for location in candidates if location.get("is_primary")),
        candidates[0],
    )
    result = {
        key: value
        for key, value in primary.items()
        if key not in {"is_primary", "locations"}
    }
    extras = [
        {
            key: value
            for key, value in location.items()
            if key != "is_primary"
        }
        for location in candidates
        if location is not primary
    ]
    if extras:
        result["locations"] = extras
    return result


def _preferred_entry(
    exact: dict | None,
    matches: list[dict],
) -> dict | None:
    if exact and exact.get("method") in {"manual_invalid", "ai_not_found"}:
        return exact
    for entry in matches:
        if entry.get("method") == "manual_invalid":
            return entry

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


def address_cache_lookup(
    entries: dict,
    *,
    publishable_only: bool = False,
) -> dict[str, dict]:
    """Index safe cache matches."""
    if publishable_only:
        entries = {
            name: trusted
            for name, entry in entries.items()
            if (trusted := _publishable_entry(entry)) is not None
        }
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


def _publishable_signatures(entry: dict | None) -> set[tuple[str, ...]]:
    return {
        _signature(location)
        for location in location_candidates(entry)
        if is_publishable_location(location)
    }


def publishable_first_entry(
    publishable_lookup: dict[str, dict],
    lookup: dict[str, dict],
    employer_name: str,
) -> tuple[dict | None, bool]:
    """Prefer a publishable entry and report whether it adds addresses."""
    entry = resolve_cache_entry(lookup, employer_name)
    trusted = resolve_cache_entry(publishable_lookup, employer_name)
    if not isinstance(trusted, dict) or trusted.get("method") in _CACHE_BLOCK_METHODS:
        return entry, False
    signatures = _publishable_signatures(trusted)
    if not signatures or _publishable_signatures(entry) == signatures:
        return entry, False
    return trusted, True


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
    path = RULES_DIR / "zip_centroids.csv"
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
