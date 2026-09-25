"""Pick the address an employment row points to: its employer's location."""
from __future__ import annotations

from fec.database.loader._base import to_native
from fec.database.loader.addresses import _akey, load_employer_locations
from fec.resolve.pipeline.location_choice import select_location


def _location_index(
    employer_locations: list[dict] | None = None,
) -> dict[str, dict]:
    """Index known locations by exact employer name."""
    grouped: dict[str, list[dict]] = {}
    locations = employer_locations
    if locations is None:
        locations = load_employer_locations()
    for location in locations:
        grouped.setdefault(location["employer_name"], []).append(location)

    lookup = {}
    for name, locations in grouped.items():
        primary = next(
            (location for location in locations if location["is_primary"]),
            None,
        )
        entry = {**(primary or {}), "locations": [
            location for location in locations if location is not primary
        ]}
        lookup[name] = entry
    return lookup


def _employment_address_id(
    row,
    employer_name,
    locations: dict,
    address_ids: dict,
):
    """Choose the address attached to one employment."""
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


def _location_employer(row, emp_id, employer_name: str | None, emp_status) -> str | None:
    """The employer whose location an employment row takes: current, else the retiree's previous."""
    if emp_id:
        return employer_name
    if emp_status == 'retired':
        return row.get('previous_employer')
    return None
