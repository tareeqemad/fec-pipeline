"""Load one leader or accomplice row: identity, address, image."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import pandas as pd

from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)


def _reset_id_sequence(conn: Any, cur: Any, table: str) -> None:
    """Reset the table's serial PK sequence to MAX(pk), discovering which column owns the sequence."""
    try:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
              AND pg_get_serial_sequence(c.relname, a.attname) IS NOT NULL
            LIMIT 1
            """,
            (table,),
        )
        row = cur.fetchone()
        if not row:
            return
        column = row[0]
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{column}'), "
            f"COALESCE(MAX({column}), 1)) FROM {table}"
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _person_value(row, field: str) -> str:
    """Read a shared person field from either leadership CSV format."""
    value = row.get(f"leader_{field}") or row.get(f"accomplice_{field}") or ""
    return value.strip()


def _person_identity(row) -> tuple[str, str, str, str, str]:
    """Return name, first, last, city, and state from either CSV format."""
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


def _boolean(row, field: str, csv_filename: str, name: str) -> bool:
    """Read a required boolean."""
    value = (row.get(field) or "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(
        f"{csv_filename}: {name!r} needs {field}=true or false"
    )


def _coordinate(row, field: str, csv_filename: str, name: str) -> float | None:
    """Parse an optional hand-maintained coordinate, warning on bad text."""
    raw = (row.get(field) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "  %s: %s -- unparseable %s %r, storing NULL",
            csv_filename, name, field, raw,
        )
        return None


def _upsert_image(cur: Any, row, donor_id: int) -> None:
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
    row,
    donor_id: int,
    csv_filename: str,
    name: str,
    city: str,
    state: str,
    upsert_donor_address,
) -> None:
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
        latitude=_coordinate(row, "address_lat", csv_filename, name),
        longitude=_coordinate(row, "address_lng", csv_filename, name),
    )


def _load_donor_linked_csv(
    conn: Any, cur: Any, csv_filename: str, table: str, insert_row,
    validate_rows=None,
) -> None:
    """Load curated donor links."""
    from .employers import _location_index

    csv_path = PROJECT_ROOT / "data" / "database" / csv_filename
    if not csv_path.exists():
        logger.info(f"  {csv_path.name} not found -- skipping {table}")
        return

    start = time.time()
    rows = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    if validate_rows is not None:
        # Fail before the first row is written, not halfway through the roster.
        validate_rows(rows)
    # The same employer locations the FEC employments were placed with.
    locations = _location_index()
    match_counts: dict[str, int] = defaultdict(int)
    inserted = skipped = 0

    for _, row in rows.iterrows():
        method = _load_person(cur, row, csv_filename, locations, insert_row)
        if method is None:
            skipped += 1
            continue
        match_counts[method] += 1
        inserted += 1

    conn.commit()
    _reset_id_sequence(conn, cur, table)
    logger.info(
        f"  {table}: {inserted} rows ({skipped} skipped), "
        f"match methods: {dict(match_counts)} ({time.time() - start:.1f}s)"
    )


def _load_person(cur, row, csv_filename: str, locations, insert_row):
    """Link one roster row to its donor; return the match method."""
    from fec.database.leadership_matcher import (
        find_or_create_donor,
        upsert_donor_address,
        upsert_leader_employment,
    )

    name, first, last, city, state = _person_identity(row)
    if not name:
        return None

    donor_id, method = find_or_create_donor(
        cur,
        (row.get("donor_key") or "").strip(),
        _boolean(row, "create_if_missing", csv_filename, name),
        name,
        first,
        last,
    )

    _upsert_image(cur, row, donor_id)
    _upsert_address(
        cur,
        row,
        donor_id,
        csv_filename,
        name,
        city,
        state,
        upsert_donor_address,
    )
    upsert_leader_employment(
        cur,
        donor_id,
        _person_value(row, "employer"),
        _person_value(row, "occupation"),
        state=state,
        zip_5=_person_value(row, "zip"),
        locations=locations,
    )
    insert_row(cur, row, donor_id)
    return method
