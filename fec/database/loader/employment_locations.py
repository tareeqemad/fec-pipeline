"""Choose an address for each employment."""
from __future__ import annotations

from fec.database.loader._base import to_native
from fec.database.loader.addresses import _akey, _employer_locations
from fec.resolve.locations import select_location


# Group offices by company, keeping the primary office first.
def _location_index(
    employer_locations: list[dict] | None = None,
) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for location in _employer_locations(employer_locations):
        grouped.setdefault(location["employer_name"], []).append(location)

    lookup = {}
    for name, locations in grouped.items():
        primary = next(
            (location for location in locations if location["is_primary"]),
            None,
        )
        lookup[name] = {**(primary or {}), "locations": [
            location for location in locations if location is not primary
        ]}
    return lookup


# Choose a company office or the self-employed donor address.
def _employment_address_id(
    row,
    employer_name,
    locations: dict,
    address_ids: dict,
):
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
            raise RuntimeError(
                "self-employed address was not loaded: "
                f"donor_key={row.get('donor_key')}"
            )
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
