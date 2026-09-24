"""Load leaders and key_accomplices from their hand-maintained CSVs.

Both rosters name committees by committee_short (leaders.committee_ids
'{AIPAC,DMFI}', key_accomplices.committee_id 'ZOA'); the loader resolves each
name through the committees table and refuses an unknown one.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)


def committee_lookup(cur: Any) -> dict[str, int]:
    """committee_short -> committee_id, read from the committees table the loader just filled.

    The rosters name committees by committee_short (AIPAC, DMFI, ...). The ids are a
    SERIAL assigned in committees.csv row order, so a roster must never hard-code them.
    """
    cur.execute(
        "SELECT committee_short, committee_id FROM committees WHERE committee_short IS NOT NULL"
    )
    return {short: committee_id for short, committee_id in cur.fetchall()}


def resolve_committee(
    short: str, committees: dict[str, int], csv_filename: str, who: str,
) -> int:
    """One committee_short -> committee_id; an unknown name stops the load."""
    committee_id = committees.get(short)
    if committee_id is None:
        raise ValueError(
            f"{csv_filename}: {who!r} names unknown committee {short!r}; "
            f"use one of {sorted(committees)} (committee_short in committees.csv)"
        )
    return committee_id


def leader_committee_ids(
    raw: str | None, committees: dict[str, int], who: str,
) -> list[int]:
    """Parse leaders.csv committee_ids ('{AIPAC,DMFI}') into committee ids."""
    text = (raw or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text.strip():
        return []
    ids = []
    for token in text.split(","):
        short = token.strip()
        if not short:
            raise ValueError(f"leaders.csv: {who!r} has an empty entry in committee_ids {raw!r}")
        committee_id = resolve_committee(short, committees, "leaders.csv", who)
        if committee_id in ids:
            raise ValueError(f"leaders.csv: {who!r} lists committee {short!r} twice")
        ids.append(committee_id)
    return ids


def accomplice_committee_id(
    raw: str | None, committees: dict[str, int], who: str,
) -> int | None:
    """Parse key_accomplices.csv committee_id ('ZOA', or blank for none)."""
    short = (raw or "").strip()
    if not short:
        return None
    return resolve_committee(short, committees, "key_accomplices.csv", who)


def load_leadership(conn: Any, cur: Any) -> None:
    """Load leaders.csv into leaders (donor_id) plus the leader_committees M:N junction."""
    committees = committee_lookup(cur)

    def _validate(rows):
        for _, row in rows.iterrows():
            leader_committee_ids(
                row.get("committee_ids"), committees, (row.get("leader_name") or "").strip(),
            )

    def _insert(cur, row, donor_id):
        kept = leader_committee_ids(
            row.get("committee_ids"), committees, (row.get("leader_name") or "").strip(),
        )

        # Reuse existing leaders.
        cur.execute(
            """
            INSERT INTO leaders (donor_id) VALUES (%s)
            ON CONFLICT (donor_id) DO UPDATE SET donor_id = EXCLUDED.donor_id
            RETURNING leader_id
            """,
            (donor_id,),
        )
        leader_id = cur.fetchone()[0]

        # Rebuild committee links.
        cur.execute("DELETE FROM leader_committees WHERE leader_id = %s", (leader_id,))
        if kept:
            execute_values(
                cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, committee_id) for committee_id in kept],
            )

    _load_donor_linked_csv(conn, cur, "leaders.csv", "leaders", _insert, _validate)


def load_key_accomplices(conn: Any, cur: Any) -> None:
    """Load key_accomplices.csv into key_accomplices (donor_id FK + card fields)."""
    committees = committee_lookup(cur)

    def _txt(row, col):
        value = (row.get(col) or "").strip()
        return value or None

    def _validate(rows):
        for _, row in rows.iterrows():
            accomplice_committee_id(
                row.get("committee_id"), committees, (row.get("accomplice_name") or "").strip(),
            )

    def _insert(cur, row, donor_id):
        # Ignore blank editorial fields.
        who = (row.get("accomplice_name") or "").strip()
        committee_id = accomplice_committee_id(row.get("committee_id"), committees, who)
        sort_raw = (row.get("display_order") or "").strip()
        display_order = 0
        if sort_raw:
            if sort_raw.lstrip("-").isdigit():
                display_order = int(sort_raw)
            else:
                logger.warning("  key_accomplices.csv: %s -- unparseable display_order %r, using 0",
                               who, sort_raw)
        cur.execute(
            """
            INSERT INTO key_accomplices
                (donor_id, sign, subtitle, body_text,
                 committee_id, display_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (donor_id) DO UPDATE SET
                sign          = EXCLUDED.sign,
                subtitle      = EXCLUDED.subtitle,
                body_text     = EXCLUDED.body_text,
                committee_id  = EXCLUDED.committee_id,
                display_order = EXCLUDED.display_order
            """,
            (
                donor_id,
                _txt(row, "sign"),
                _txt(row, "subtitle"),
                _txt(row, "body_text"),
                committee_id,
                display_order,
            ),
        )

    _load_donor_linked_csv(conn, cur, "key_accomplices.csv", "key_accomplices", _insert, _validate)


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
