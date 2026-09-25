"""Choose the closest known employer location."""
from __future__ import annotations


from fec.cleaning.street_text import _normalize_street
from fec.cleaning.employer_synonyms import canonical_key

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


# coerce a value to a stripped string
def _text(value) -> str:
    return str(value or "").strip()


# check whether a location dict carries a usable address
def _usable(location: dict | None) -> bool:
    return bool(location and (
        _text(location.get("employer_address"))
        or (
            location.get("method") == "manual_override"
            and _text(location.get("employer_city"))
            and _text(location.get("employer_state"))
        )
    ))


# check whether an address has auditable evidence
def is_publishable_location(location: dict | None) -> bool:
    """Return whether an address has auditable evidence."""
    if not _usable(location):
        return False
    method = _text(location.get("method"))
    return method == "manual_override" or method.endswith("_search")


# build an uppercase field tuple identifying one location
def _signature(location: dict) -> tuple[str, ...]:
    return tuple(_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


# build a signature for a manual entry's primary address
def _manual_signature(entry: dict) -> tuple[str, ...]:
    primary = location_candidates(entry)[0]
    street = _normalize_street(primary.get("employer_address"))
    return (
        _text(street).upper(),
        _text(primary.get("employer_city")).upper(),
        _text(primary.get("employer_state")).upper(),
        _text(primary.get("employer_zip")).upper(),
    )


# collect unique usable locations from one cache entry
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


# keep blockers, or return only an entry's publishable locations
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


# choose the best cache entry among exact and canonical matches
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


# index safe cache matches by exact name and canonical key
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


# read one cache match by exact name or canonical key
def resolve_cache_entry(
    lookup: dict[str, dict],
    employer_name: str,
) -> dict | None:
    """Read one cache match."""
    exact = str(employer_name).strip().upper()
    return lookup.get(exact) or lookup.get(canonical_key(exact))


# collect signatures of an entry's publishable locations
def _publishable_signatures(entry: dict | None) -> set[tuple[str, ...]]:
    return {
        _signature(location)
        for location in location_candidates(entry)
        if is_publishable_location(location)
    }


# prefer the publishable cache entry over the full lookup's entry
def publishable_first_entry(
    publishable_lookup: dict[str, dict],
    lookup: dict[str, dict],
    employer_name: str,
) -> tuple[dict | None, bool]:
    """The entry resolve's apply step uses, else the full lookup's entry.

    ``publishable_lookup`` is ``address_cache_lookup(cache, publishable_only=True)``
    (what apply.py resolves with) and ``lookup`` the full ``address_cache_lookup(cache)``.
    The full lookup lets an exact closed-book answer hide a grounded canonical sibling
    ('SCOTT FANE,CPA PA' vs 'SCOTT FANE CPA PA') and calls a group with a closed-book
    sibling at another address ambiguous; the publishable lookup filters closed-book
    answers first, so it finds the grounded address.

    Returns ``(entry, changed)``: ``changed`` is True only when the publishable
    lookup found publishable addresses the full lookup's entry does not have, so
    the caller can check that answer before publishing it. A blocker
    (ai_not_found, manual_invalid, manual_review) is never overruled here.
    """
    entry = resolve_cache_entry(lookup, employer_name)
    trusted = resolve_cache_entry(publishable_lookup, employer_name)
    if not isinstance(trusted, dict) or trusted.get("method") in _CACHE_BLOCK_METHODS:
        return entry, False
    signatures = _publishable_signatures(trusted)
    if not signatures or _publishable_signatures(entry) == signatures:
        return entry, False
    return trusted, True


