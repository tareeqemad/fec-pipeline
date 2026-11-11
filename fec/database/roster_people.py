"""Save the people of the leader and accomplice rosters: donor, address, job and image."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from fec.cleaning.work.employer_status import classify_employer_status, is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.log import get_logger
from fec.common.settings import REFERENCE_DIR
from fec.database.offices import choose_office, office_address, workplaces_by_company
from fec.matching.identity_rules import resolve_donor_key

logger = get_logger(__name__)


def find_or_create_donor(
    cur: Any,
    donor_key: str,
    create_if_missing: bool,
    name: str,
    first: str,
    last: str,
) -> int:
    """Match a roster donor by its saved or merged key."""
    donor_key = donor_key.strip()
    if not donor_key:
        raise ValueError(f"Missing donor_key for {name!r}")

    # Follow merged keys so old roster keys create no duplicates.
    resolved_key = resolve_donor_key(donor_key)

    cur.execute("SELECT donor_id FROM donors WHERE donor_key = %s", (resolved_key,))
    row = cur.fetchone()
    if row:
        return row[0]

    if not create_if_missing:
        raise ValueError(f"Unknown donor_key {donor_key!r} for {name!r}; update the editorial CSV")

    first_name = (first or "").strip().upper() or None
    last_name = (last or "").strip().upper() or None
    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (resolved_key, first_name, last_name),
    )
    return cur.fetchone()[0]


def _find_or_insert_address(cur: Any, fields: tuple, latitude: float | None, longitude: float | None) -> int:
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
        fields,
    )
    row = cur.fetchone()
    if row:
        address_id = row[0]
        if latitude is not None:
            cur.execute(
                "UPDATE addresses SET latitude=%s, longitude=%s WHERE address_id=%s AND latitude IS NULL",
                (latitude, longitude, address_id),
            )
        return address_id

    cur.execute(
        """
        INSERT INTO addresses
            (street_1, street_2, city, state_code, zip_code, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING address_id
        """,
        (*fields, latitude, longitude),
    )
    return cur.fetchone()[0]


def upsert_donor_address(
    cur: Any,
    donor_id: int,
    address_parts: tuple,
    latitude: float | None = None,
    longitude: float | None = None,
) -> None:
    """Link the curated address to its donor.

    address_parts holds street_1, street_2, city, state and zip_5.
    """
    # Treat blank strings as missing.
    fields = tuple((value or "").strip() or None for value in address_parts)
    if all(value is None for value in fields):
        return
    address_id = _find_or_insert_address(cur, fields, latitude, longitude)

    cur.execute(
        "SELECT 1 FROM donor_addresses WHERE donor_id = %s AND address_id = %s LIMIT 1",
        (donor_id, address_id),
    )
    if cur.fetchone():
        return
    cur.execute(
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES (%s, %s)",
        (donor_id, address_id),
    )


def roster_office_address_id(
    cur: Any,
    employer: str,
    locations: dict | None,
    state: str | None = "",
    zip_5: str | None = "",
    city: str | None = "",
) -> int | None:
    """Same-city office, then a nearby state office, else default."""
    if not employer or not locations:
        return None
    location = choose_office(locations.get(employer, []), zip_5 or "", state or "", city or "")
    if not location:
        return None
    return _find_or_insert_address(cur, office_address(location), location["latitude"], location["longitude"])


def _read_office_coordinate(value: Any) -> float | None:
    try:
        return float(value) if str(value or "").strip() else None
    except ValueError:
        return None


def upsert_leader_employment(
    cur: Any,
    donor_id: int,
    employer: str | None,
    occupation: str | None = None,
    *,
    place: tuple[str | None, str | None, str | None] = ("", "", ""),
    locations: dict | None = None,
) -> None:
    """Add roster employment only when the donor has none.

    place holds the donor's state, zip_5 and city.
    """
    employer_name = (employer or "").strip()
    occupation = (occupation or "").strip() or None
    if not employer_name and not occupation:
        return

    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    employer_id, status = _find_roster_employer(cur, employer_name)
    category_id = _find_occupation_category_id(cur, occupation)

    address_id = None
    if status == "active":
        state, zip_5, city = place
        address_id = roster_office_address_id(cur, employer_name, locations, state, zip_5, city)

    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, occupation_category_id,
             employer_status, address_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (donor_id, employer_id, occupation, category_id, status, address_id),
    )


def _find_roster_employer(cur: Any, employer_name: str) -> tuple[int | None, str]:
    if not employer_name or not is_real_employer(employer_name):
        status = classify_employer_status(employer_name) if employer_name else "missing"
        return None, status

    cur.execute(
        "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        (employer_name,),
    )
    cur.execute("SELECT employer_id FROM employers WHERE name = %s", (employer_name,))
    return cur.fetchone()[0], "active"


def _find_occupation_category_id(cur: Any, occupation: str | None) -> int | None:
    if not occupation:
        return None
    category = categorize_occupations(pd.Series([occupation])).iloc[0]
    cur.execute("SELECT occupation_category_id FROM occupation_categories WHERE name = %s", (category,))
    category_row = cur.fetchone()
    return category_row[0] if category_row else None


def _read_person_value(row: pd.Series, field: str) -> str:
    value = row.get(f"leader_{field}") or row.get(f"accomplice_{field}") or ""
    return value.strip()


def _read_person_identity(row: pd.Series) -> tuple[str, str, str, str, str]:
    name = _read_person_value(row, "name")
    first = _read_person_value(row, "first_name")
    last = _read_person_value(row, "last_name")

    if (not first or not last) and "," in name:
        last_part, _, first_part = name.partition(",")
        last = last or last_part.strip()
        first = first or first_part.strip()

    return (
        name,
        first,
        last,
        _read_person_value(row, "city"),
        _read_person_value(row, "state"),
    )


def _read_boolean(row: pd.Series, field: str, csv_filename: str, name: str) -> bool:
    value = (row.get(field) or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{csv_filename}: {name!r} needs {field}=true or false")


def _read_roster_coordinate(row: pd.Series, field: str, csv_filename: str, name: str) -> float | None:
    coordinate_text = (row.get(field) or "").strip()
    if not coordinate_text:
        return None
    try:
        return float(coordinate_text)
    except ValueError:
        logger.warning(
            "  %s: %s -- unparseable %s %r, storing NULL",
            csv_filename,
            name,
            field,
            coordinate_text,
        )
        return None


def _upsert_image(cur: Any, row: pd.Series, donor_id: int) -> None:
    image_path = (row.get("image_path") or "").strip()
    if not image_path:
        return
    cur.execute(
        "INSERT INTO donor_images (donor_id, image_path) VALUES (%s, %s) "
        "ON CONFLICT (donor_id) DO UPDATE SET image_path = EXCLUDED.image_path",
        (donor_id, image_path),
    )


def _upsert_address(
    cur: Any,
    row: pd.Series,
    donor_id: int,
    csv_filename: str,
    name: str,
) -> None:
    street_1 = _read_person_value(row, "street_1")
    city = _read_person_value(row, "city")
    if not street_1 and not city:
        return

    address_parts = (
        street_1,
        _read_person_value(row, "street_2"),
        city,
        _read_person_value(row, "state"),
        _read_person_value(row, "zip"),
    )
    upsert_donor_address(
        cur,
        donor_id,
        address_parts,
        latitude=_read_roster_coordinate(row, "address_lat", csv_filename, name),
        longitude=_read_roster_coordinate(row, "address_lng", csv_filename, name),
    )


def load_roster_csv(
    cur: Any,
    csv_filename: str,
    table: str,
    insert_row: Callable[[Any, pd.Series, int], None],
    validate_rows: Callable[[pd.DataFrame], None],
    offices: list[dict],
) -> None:
    """Validate and load the roster, linking rows to donors."""
    csv_path = REFERENCE_DIR / csv_filename
    if not csv_path.exists():
        logger.info(f"  {csv_path.name} not found -- skipping {table}")
        return

    rows = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    validate_rows(rows)
    locations = workplaces_by_company(offices)
    skipped = 0

    for _, row in rows.iterrows():
        skipped += not _load_person(cur, row, csv_filename, locations, insert_row)

    if skipped:
        logger.warning("WARNING: %s has %s rows without a name; skipped", csv_path.name, skipped)


def _load_person(
    cur: Any,
    row: pd.Series,
    csv_filename: str,
    locations: dict[str, dict],
    insert_row: Callable[[Any, pd.Series, int], None],
) -> bool:
    name, first, last, city, state = _read_person_identity(row)
    if not name:
        return False

    donor_key = (row.get("donor_key") or "").strip()
    create_if_missing = _read_boolean(row, "create_if_missing", csv_filename, name)
    donor_id = find_or_create_donor(cur, donor_key, create_if_missing, name, first, last)

    _upsert_image(cur, row, donor_id)
    _upsert_address(cur, row, donor_id, csv_filename, name)
    upsert_leader_employment(
        cur,
        donor_id,
        _read_person_value(row, "employer"),
        _read_person_value(row, "occupation"),
        place=(state, _read_person_value(row, "zip"), city),
        locations=locations,
    )
    insert_row(cur, row, donor_id)
    return True
