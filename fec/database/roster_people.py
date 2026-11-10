"""Save the people of the leader and accomplice rosters: donor, address, job and image."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from fec.cleaning.work.employer_status import classify_employer_status, is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.log import get_logger
from fec.common.settings import REFERENCE_DIR
from fec.database.offices import offices_by_company, select_location
from fec.matching.identity_rules import resolve_donor_key


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

    cur.execute(
        """
        INSERT INTO donors (donor_key, entity_type, first_name, last_name)
        VALUES (%s, 'INDIVIDUAL', %s, %s)
        RETURNING donor_id
        """,
        (resolved_key, (first or "").strip().upper() or None, (last or "").strip().upper() or None),
    )
    return cur.fetchone()[0]


def _address_id(cur: Any, fields: tuple, latitude: float | None, longitude: float | None) -> int:
    """Reuse an address or insert it with its coordinates."""
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
    street_1: str | None,
    street_2: str | None,
    city: str | None,
    state: str | None,
    zip_5: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> None:
    """Link the curated address to its donor."""
    # Treat blank strings as missing.
    fields = tuple((value or "").strip() or None for value in (street_1, street_2, city, state, zip_5))
    if all(value is None for value in fields):
        return
    address_id = _address_id(cur, fields, latitude, longitude)

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
    location = select_location(locations.get(employer), zip_5 or "", state or "", city or "")
    if not location:
        return None

    fields = tuple(
        location.get(field) or None
        for field in ("employer_address", "", "employer_city", "employer_state", "employer_zip")
    )
    return _address_id(
        cur,
        fields,
        _office_coordinate(location.get("employer_latitude")),
        _office_coordinate(location.get("employer_longitude")),
    )


def _office_coordinate(value: Any) -> float | None:
    """Read an optional coordinate, ignoring invalid text."""
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
    state: str | None = "",
    zip_5: str | None = "",
    city: str | None = "",
    locations: dict | None = None,
) -> None:
    """Add roster employment only when the donor has none."""
    employer_name = (employer or "").strip()
    occupation = (occupation or "").strip() or None
    if not employer_name and not occupation:
        return

    cur.execute("SELECT 1 FROM donor_employments WHERE donor_id = %s LIMIT 1", (donor_id,))
    if cur.fetchone():
        return

    if not employer_name or not is_real_employer(employer_name):
        employer_id = None
        status = classify_employer_status(employer_name) if employer_name else "missing"
    else:
        cur.execute(
            "INSERT INTO employers (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
            (employer_name,),
        )
        cur.execute("SELECT employer_id FROM employers WHERE name = %s", (employer_name,))
        employer_id = cur.fetchone()[0]
        status = "active"

    category_id = None
    if occupation:
        category = categorize_occupations(pd.Series([occupation])).iloc[0]
        cur.execute("SELECT occupation_category_id FROM occupation_categories WHERE name = %s", (category,))
        found = cur.fetchone()
        category_id = found[0] if found else None

    address_id = (
        roster_office_address_id(cur, employer_name, locations, state, zip_5, city) if status == "active" else None
    )

    cur.execute(
        """
        INSERT INTO donor_employments
            (donor_id, employer_id, occupation, occupation_category_id,
             employer_status, address_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (donor_id, employer_id, occupation, category_id, status, address_id),
    )


logger = get_logger(__name__)


def _person_value(row: pd.Series, field: str) -> str:
    """Read a field from either roster format."""
    value = row.get(f"leader_{field}") or row.get(f"accomplice_{field}") or ""
    return value.strip()


def _person_identity(row: pd.Series) -> tuple[str, str, str, str, str]:
    """Read the roster name and place, filling split names."""
    name = _person_value(row, "name")
    first = _person_value(row, "first_name")
    last = _person_value(row, "last_name")

    if (not first or not last) and "," in name:
        last_part, _, first_part = name.partition(",")
        last = last or last_part.strip()
        first = first or first_part.strip()

    return (
        name,
        first,
        last,
        _person_value(row, "city"),
        _person_value(row, "state"),
    )


def _boolean(row: pd.Series, field: str, csv_filename: str, name: str) -> bool:
    """Accept true or false; reject missing or invalid values."""
    value = (row.get(field) or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{csv_filename}: {name!r} needs {field}=true or false")


def _roster_coordinate(row: pd.Series, field: str, csv_filename: str, name: str) -> float | None:
    """Read an optional coordinate and warn about invalid text."""
    raw = (row.get(field) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "  %s: %s -- unparseable %s %r, storing NULL",
            csv_filename,
            name,
            field,
            raw,
        )
        return None


def _upsert_image(cur: Any, row: pd.Series, donor_id: int) -> None:
    """Save the donor image when a path is provided."""
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
    city: str,
    state: str,
) -> None:
    """Save the roster address and optional coordinates."""
    street_1 = _person_value(row, "street_1")
    if not street_1 and not city:
        return

    upsert_donor_address(
        cur,
        donor_id,
        street_1=street_1,
        street_2=_person_value(row, "street_2"),
        city=city,
        state=state,
        zip_5=_person_value(row, "zip"),
        latitude=_roster_coordinate(row, "address_lat", csv_filename, name),
        longitude=_roster_coordinate(row, "address_lng", csv_filename, name),
    )


def load_roster_csv(
    cur: Any,
    csv_filename: str,
    table: str,
    insert_row: Callable[[Any, pd.Series, int], None],
    validate_rows: Callable[[pd.DataFrame], None] | None = None,
) -> None:
    """Validate and load the roster, linking rows to donors."""
    csv_path = REFERENCE_DIR / csv_filename
    if not csv_path.exists():
        logger.info(f"  {csv_path.name} not found -- skipping {table}")
        return

    rows = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if validate_rows is not None:
        # Validate the whole roster before writing any row.
        validate_rows(rows)
    # Use the same office choices as the FEC loader.
    locations = offices_by_company()
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
    """Save one roster donor; False for a row without a name."""
    name, first, last, city, state = _person_identity(row)
    if not name:
        return False

    donor_id = find_or_create_donor(
        cur,
        (row.get("donor_key") or "").strip(),
        _boolean(row, "create_if_missing", csv_filename, name),
        name,
        first,
        last,
    )

    _upsert_image(cur, row, donor_id)
    _upsert_address(cur, row, donor_id, csv_filename, name, city, state)
    upsert_leader_employment(
        cur,
        donor_id,
        _person_value(row, "employer"),
        _person_value(row, "occupation"),
        state=state,
        city=city,
        zip_5=_person_value(row, "zip"),
        locations=locations,
    )
    insert_row(cur, row, donor_id)
    return True
