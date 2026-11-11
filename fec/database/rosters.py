"""Load the leader and key-accomplice rosters: each person's donor, image, address and job, then the roster row."""

from __future__ import annotations

from typing import Any

import pandas as pd

from fec.cleaning.work.employer_status import classify_employer_status, is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.log import get_logger
from fec.common.settings import REFERENCE_DIR
from fec.database.offices import choose_office, office_address, workplaces_by_company
from fec.database.reference_tables import committee_ids_by_short_name
from fec.matching.identity_rules import resolve_donor_key

logger = get_logger(__name__)


def load_leaders(cur: Any, offices: list[dict]) -> None:
    """Load leaders.csv: each leader and the committees they lead."""
    committee_ids = committee_ids_by_short_name(cur)
    workplaces = workplaces_by_company(offices)
    for person in _read_roster("leaders.csv", "leader_"):
        donor_id = _load_person(cur, person, workplaces)
        cur.execute("INSERT INTO leaders (donor_id) VALUES (%s) RETURNING leader_id", (donor_id,))
        leader_id = cur.fetchone()[0]
        for short_name in _read_committee_names(person["committee_ids"]):
            cur.execute(
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES (%s, %s)",
                (leader_id, _find_committee_id(committee_ids, short_name, person)),
            )


def load_key_accomplices(cur: Any, offices: list[dict]) -> None:
    """Load key_accomplices.csv: each person and the text of their card."""
    committee_ids = committee_ids_by_short_name(cur)
    workplaces = workplaces_by_company(offices)
    for person in _read_roster("key_accomplices.csv", "accomplice_"):
        donor_id = _load_person(cur, person, workplaces)
        committee_id = None
        if person["committee_id"]:
            committee_id = _find_committee_id(committee_ids, person["committee_id"], person)
        cur.execute(
            """
            INSERT INTO key_accomplices (donor_id, sign, subtitle, body_text, committee_id, display_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                donor_id,
                person["sign"] or None,
                person["subtitle"] or None,
                person["body_text"] or None,
                committee_id,
                int(person["display_order"] or 0),
            ),
        )


def _read_roster(csv_name: str, column_prefix: str) -> list[dict]:
    """Read one roster: a record per named person, values trimmed, columns named without the roster's prefix."""
    people = pd.read_csv(REFERENCE_DIR / csv_name, dtype=str, keep_default_na=False)
    people.columns = people.columns.str.removeprefix(column_prefix)
    for column in people.columns:
        people[column] = people[column].str.strip()

    unnamed = people["name"].eq("")
    if unnamed.any():
        logger.warning("WARNING: %s has %s rows without a name; skipped", csv_name, int(unnamed.sum()))
    people = people[~unnamed]
    undecided = people[~people["create_if_missing"].str.lower().isin(["true", "false"])]
    if len(undecided):
        raise ValueError(f"{csv_name}: {undecided['name'].tolist()} need create_if_missing=true or false")
    return people.to_dict("records")


def _read_committee_names(text: str) -> list[str]:
    # Written as {AIPAC,DMFI}; blank for a leader of no committee.
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text.strip():
        return []
    return [short_name.strip() for short_name in text.split(",")]


def _find_committee_id(committee_ids: dict[str, int], short_name: str, person: dict) -> int:
    if short_name not in committee_ids:
        raise ValueError(
            f"{person['name']!r} names unknown committee {short_name!r}; "
            f"use one of {sorted(committee_ids)} (committee_short in committees.csv)"
        )
    return committee_ids[short_name]


def _number_or_none(text: str) -> float | None:
    # A value that is not a number stops the load, so that a typing slip is seen.
    return float(text) if text else None


def _load_person(cur: Any, person: dict, workplaces: dict) -> int:
    """Save the person's donor, image, address and job; return the donor ID."""
    donor_id = _find_or_create_donor(cur, person)
    if person["image_path"]:
        # A person on both rosters keeps the image of the roster loaded last.
        cur.execute(
            "INSERT INTO donor_images (donor_id, image_path) VALUES (%s, %s) "
            "ON CONFLICT (donor_id) DO UPDATE SET image_path = EXCLUDED.image_path",
            (donor_id, person["image_path"]),
        )
    if person["street_1"] or person["city"]:
        _link_donor_address(cur, donor_id, person)
    if person["employer"] or person["occupation"]:
        _add_first_employment(cur, donor_id, person, workplaces)
    return donor_id


def _find_or_create_donor(cur: Any, person: dict) -> int:
    name = person["name"]
    if not person["donor_key"]:
        raise ValueError(f"Missing donor_key for {name!r}")
    # Follow merged keys so old roster keys create no duplicates.
    donor_key = resolve_donor_key(person["donor_key"])
    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (donor_key,))
    donor = cur.fetchone()
    if donor:
        return donor[0]
    if person["create_if_missing"].lower() != "true":
        raise ValueError(f"Unknown donor_key {person['donor_key']!r} for {name!r}; update the editorial CSV")

    first, last = person.get("first_name", ""), person.get("last_name", "")
    if (not first or not last) and "," in name:
        # leaders.csv has no name columns; its names are written LAST, FIRST.
        last_part, _, first_part = name.partition(",")
        last = last or last_part.strip()
        first = first or first_part.strip()
    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (donor_key, first.upper() or None, last.upper() or None),
    )
    return cur.fetchone()[0]


def _find_or_insert_address(cur: Any, address: tuple, latitude: float | None, longitude: float | None) -> int:
    """Return the ID of the address (street_1, street_2, city, state, zip code); add coordinates it lacks."""
    cur.execute(
        """
        SELECT address_id FROM addresses
        WHERE street_1   IS NOT DISTINCT FROM %s
          AND street_2   IS NOT DISTINCT FROM %s
          AND city       IS NOT DISTINCT FROM %s
          AND state_code IS NOT DISTINCT FROM %s
          AND zip_code   IS NOT DISTINCT FROM %s
        ORDER BY address_id
        LIMIT 1
        """,
        address,
    )
    found = cur.fetchone()
    if found is None:
        cur.execute(
            """
            INSERT INTO addresses (street_1, street_2, city, state_code, zip_code, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING address_id
            """,
            (*address, latitude, longitude),
        )
        return cur.fetchone()[0]
    if latitude is not None:
        cur.execute(
            "UPDATE addresses SET latitude = %s, longitude = %s WHERE address_id = %s AND latitude IS NULL",
            (latitude, longitude, found[0]),
        )
    return found[0]


def _link_donor_address(cur: Any, donor_id: int, person: dict) -> None:
    address = tuple(person[column] or None for column in ("street_1", "street_2", "city", "state", "zip"))
    latitude = _number_or_none(person["address_lat"])
    longitude = _number_or_none(person["address_lng"])
    address_id = _find_or_insert_address(cur, address, latitude, longitude)
    cur.execute(
        """
        INSERT INTO donor_addresses (donor_id, address_id)
        SELECT %s, %s
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses WHERE donor_id = %s AND address_id = %s)
        """,
        (donor_id, address_id, donor_id, address_id),
    )


def _add_first_employment(cur: Any, donor_id: int, person: dict, workplaces: dict) -> None:
    """Give the donor the roster's job, unless the filings already gave them one."""
    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    employer, occupation = person["employer"], person["occupation"]
    employer_id, workplace_address_id = None, None
    if is_real_employer(employer):
        status = "active"
        cur.execute("INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (employer,))
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (employer,))
        employer_id = cur.fetchone()[0]
        office = choose_office(workplaces.get(employer, []), person["zip"], person["state"], person["city"])
        if office:
            workplace_address_id = _find_or_insert_address(
                cur, office_address(office), office["latitude"], office["longitude"]
            )
    else:
        status = classify_employer_status(employer)

    category_id = None
    if occupation:
        category = categorize_occupations(pd.Series([occupation])).iloc[0]
        cur.execute("SELECT occupation_category_id FROM occupation_categories WHERE name = %s", (category,))
        found = cur.fetchone()
        category_id = found[0] if found else None

    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, occupation_category_id, employer_status, address_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (donor_id, employer_id, occupation or None, category_id, status, workplace_address_id),
    )
