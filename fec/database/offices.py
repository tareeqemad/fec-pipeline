"""Company offices: read the publishable ones and choose the office a donor most likely works at."""

from __future__ import annotations

import csv
from functools import lru_cache

import pandas as pd

from fec.common.settings import REFERENCE_DIR
from fec.geocoding.coordinate_checks import EARTH_RADIUS_MILES, great_circle

# How far a company address is proven; only the first two are published.
ADDRESS_TRUST_VALUES = frozenset({"verified", "grounded", "corroborated", "uncorroborated"})

PUBLISHABLE_ADDRESS_TRUST = frozenset({"verified", "grounded"})


def _text_or_none(text: str) -> str | None:
    return text if text.strip() else None


def _coordinate_or_none(text: str) -> float | None:
    return float(text) if text.strip() else None


def read_employer_offices(locations: pd.DataFrame) -> list[dict]:
    """Return the publishable offices in file order, each company with a primary office."""
    offices = []
    for row in locations.to_dict("records"):
        street = _text_or_none(row["employer_address"])
        city = _text_or_none(row["employer_city"])
        state = _text_or_none(row["employer_state"])
        # A town alone is a location; a row with neither street nor town is not.
        if row["address_trust"] not in PUBLISHABLE_ADDRESS_TRUST or (street is None and city is None):
            continue
        checked_by_hand = row["address_source"] == "manual" and row["address_trust"] == "verified"
        offices.append(
            {
                "employer_name": row["employer_name"],
                "street": street,
                "city": city,
                "state": state,
                "zip_code": _text_or_none(row["employer_zip"]),
                "latitude": _coordinate_or_none(row["employer_latitude"]),
                "longitude": _coordinate_or_none(row["employer_longitude"]),
                "is_primary": row["is_primary"].lower() == "true",
                # A donor is placed only at a street address, or at a town checked by hand.
                "is_workplace": street is not None or (checked_by_hand and city is not None and state is not None),
            }
        )

    # When a company's primary office is not publishable, its first publishable office becomes primary.
    companies_with_primary = {office["employer_name"] for office in offices if office["is_primary"]}
    for office in offices:
        if office["employer_name"] not in companies_with_primary:
            office["is_primary"] = True
            companies_with_primary.add(office["employer_name"])
    return offices


def office_address(office: dict) -> tuple:
    """Return the office as street_1, street_2, city, state and zip code; an office has no second street line."""
    return (office["street"], None, office["city"], office["state"], office["zip_code"])


def workplaces_by_company(offices: list[dict]) -> dict[str, list[dict]]:
    """Group the offices a donor can be placed at by company name."""
    workplaces: dict[str, list[dict]] = {}
    for office in offices:
        if office["is_workplace"]:
            workplaces.setdefault(office["employer_name"], []).append(office)
    return workplaces


@lru_cache(maxsize=1)
def read_zip_centroids() -> dict[str, tuple[float, float]]:
    """Read the centre point of each ZIP code from zip_centroids.csv."""
    with open(REFERENCE_DIR / "zip_centroids.csv", newline="", encoding="utf-8") as handle:
        return {row["zip"].zfill(5): (float(row["lat"]), float(row["lng"])) for row in csv.DictReader(handle)}


def _normalize(text: str | None) -> str:
    return (text or "").strip().upper()


def choose_office(workplaces: list[dict], donor_zip: str, donor_state: str, donor_city: str) -> dict | None:
    """Choose the company office in the donor's city, else the nearest in the donor's state, else the primary."""
    if not workplaces:
        return None
    donor_state, donor_city = _normalize(donor_state), _normalize(donor_city)
    in_state = [office for office in workplaces if _normalize(office["state"]) == donor_state]
    if not in_state:
        primary = [office for office in workplaces if office["is_primary"]]
        return (primary or workplaces)[0]

    in_city = [office for office in in_state if donor_city and _normalize(office["city"]) == donor_city]
    nearby = in_city or in_state
    centroids = read_zip_centroids()
    donor_point = centroids.get(_normalize(donor_zip)[:5])
    ranks = [_rank_office(office, donor_point, centroids) for office in nearby]
    return nearby[ranks.index(min(ranks))]


def _rank_office(office: dict, donor_point: tuple | None, centroids: dict) -> tuple:
    # Lowest is best: the nearest office, offices of unknown distance after those,
    # then the primary office, then the address so that the choice never depends on file order.
    address = tuple(_normalize(office[part]) for part in ("street", "city", "state", "zip_code"))
    office_point = centroids.get(_normalize(office["zip_code"])[:5])
    if donor_point is None or office_point is None:
        return (1, 0.0, not office["is_primary"], address)
    distance = great_circle(donor_point, office_point, EARTH_RADIUS_MILES)
    return (0, distance, not office["is_primary"], address)
