"""Company offices: which ones are publishable, and which one a donor uses."""
from __future__ import annotations

import csv
from functools import lru_cache
from typing import Any

import pandas as pd

from fec.database.loader._base import to_native
from fec.env import REFERENCE_DIR
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


def _text(value) -> str:
    return str(value or "").strip()


# Convert missing and whitespace-only values to None.
def _blank_to_none(value):
    value = to_native(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


# Read publishable company offices from the employer_locations rows.
def load_employer_locations(frame: pd.DataFrame) -> list[dict]:
    locations = []
    for row in frame.to_dict("records"):
        street = _blank_to_none(row.get("employer_address"))
        city = _blank_to_none(row.get("employer_city"))
        # Allow town-only locations; skip rows with neither street nor town.
        if street is None and city is None:
            continue
        if row.get("address_trust") not in PUBLISHABLE_ADDRESS_TRUST:
            continue
        locations.append({
            "employer_name": row["employer_name"],
            "employer_address": street,
            "employer_city": city,
            "employer_state": _blank_to_none(row.get("employer_state")),
            "employer_zip": _blank_to_none(row.get("employer_zip")),
            "employer_latitude": row.get("employer_latitude"),
            "employer_longitude": row.get("employer_longitude"),
            "is_primary": str(row.get("is_primary")).lower() == "true",
            "method": "manual_override" if (
                row.get("address_source") == "manual"
                and row.get("address_trust") == "verified"
            ) else "",
        })
    return locations


# An office with a street, or a reviewed town-only office.
def _usable(location: dict) -> bool:
    return bool(
        _text(location.get("employer_address"))
        or (
            location.get("method") == "manual_override"
            and _text(location.get("employer_city"))
            and _text(location.get("employer_state"))
        )
    )


def _signature(location: dict) -> tuple[str, ...]:
    return tuple(_text(location.get(field)).upper() for field in ADDRESS_FIELDS)


# Each company's usable offices, the primary office first, one per address.
def location_index(employer_locations: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, dict[tuple, dict]] = {}
    for location in sorted(employer_locations, key=lambda l: not l["is_primary"]):
        if _usable(location):
            index.setdefault(location["employer_name"], {}).setdefault(_signature(location), location)
    return {name: list(offices.values()) for name, offices in index.items()}


@lru_cache(maxsize=1)
def _zip_centroids() -> dict[str, tuple[float, float]]:
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
    candidates: list[dict],
    donor_zip: str = "",
    donor_state: str = "",
    donor_city: str = "",
) -> dict | None:
    """Same city and state, then the nearest state office."""
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

    donor_city = _text(donor_city).upper()
    same_city = [
        location for location in same_state
        if donor_city and _text(location.get("employer_city")).upper() == donor_city
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


# The address key of an office, as the address dimension stores it.
def office_address_key(location: dict) -> tuple:
    from fec.database.loader.addresses import address_key

    return address_key(
        location["employer_address"], None,
        location["employer_city"], location["employer_state"], location["employer_zip"],
    )


# The address of one employment: the donor's own when self-employed, else
# the company office nearest the donor.
def employment_address_id(
    status: str, employer_name, donor_address: tuple, donor_zip, donor_state, donor_city,
    offices: dict[str, list[dict]], address_ids: dict[tuple, int], donor_key: Any = None,
) -> int | None:
    from fec.database.loader.addresses import address_key

    if status == "self_employed":
        address = address_key(*donor_address)
        if not any(address):
            return None
        address_id = address_ids.get(address)
        if address_id is None:
            raise RuntimeError(f"self-employed address was not loaded: donor_key={donor_key}")
        return address_id
    if status == "not_employed":
        return None

    name = _text(to_native(employer_name))
    location = select_location(
        offices.get(name, []), _text(to_native(donor_zip)),
        _text(to_native(donor_state)), _text(to_native(donor_city)),
    )
    if not location:
        return None
    address_id = address_ids.get(office_address_key(location))
    if address_id is None:
        raise RuntimeError(f"employer address was not loaded: {name!r}")
    return address_id
