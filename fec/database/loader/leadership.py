"""Load leaders and key_accomplices from their hand-maintained CSVs."""
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


def load_leadership(conn: Any, cur: Any) -> None:
    """Load leaders.csv into leaders (donor_id) plus the leader_committees M:N junction."""
    # A CSV id pointing at a missing committee is skipped with a warning, not an FK abort.
    cur.execute("SELECT committee_id FROM committees")
    valid_committees = {row[0] for row in cur.fetchall()}

    def _insert(cur, row, donor_id):
        # committee_ids is a Postgres array literal like "{1,2}"; blank is valid,
        # non-blank unparseable tokens warn (hand-maintained file -- warn, don't raise).
        raw = (row.get("committee_ids") or "").strip().strip("{}")
        wanted = []
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if token.lstrip("-").isdigit():
                wanted.append(int(token))
            else:
                logger.warning("  leaders.csv: %s -- unparseable committee_ids token %r, skipping it",
                               (row.get("leader_name") or "").strip(), token)
        kept = [committee_id for committee_id in wanted if committee_id in valid_committees]
        dropped = [committee_id for committee_id in wanted if committee_id not in valid_committees]
        if dropped:
            logger.warning("  leader donor_id=%s: skipping unknown committee_ids %s", donor_id, dropped)

        # One leaders row per donor; the no-op DO UPDATE lets RETURNING fetch the
        # leader_id whether the row was inserted or already existed.
        cur.execute(
            """
            INSERT INTO leaders (donor_id) VALUES (%s)
            ON CONFLICT (donor_id) DO UPDATE SET donor_id = EXCLUDED.donor_id
            RETURNING leader_id
            """,
            (donor_id,),
        )
        leader_id = cur.fetchone()[0]

        # Reset this leader's committee links, then re-add (idempotent on re-run).
        cur.execute("DELETE FROM leader_committees WHERE leader_id = %s", (leader_id,))
        if kept:
            execute_values(
                cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, committee_id) for committee_id in kept],
            )

    _load_donor_linked_csv(conn, cur, "leaders.csv", "leaders", _insert)


def load_key_accomplices(conn: Any, cur: Any) -> None:
    """Load key_accomplices.csv into key_accomplices (donor_id FK + card fields)."""
    def _txt(row, col):
        value = (row.get(col) or "").strip()
        return value or None

    def _insert(cur, row, donor_id):
        # Blank cells are valid editorial state; non-blank garbage warns, doesn't raise.
        who = (row.get("accomplice_name") or "").strip()
        committee_raw = (row.get("committee_id") or "").strip()
        committee_id = None
        if committee_raw:
            if committee_raw.isdigit():
                committee_id = int(committee_raw)
            else:
                logger.warning("  key_accomplices.csv: %s -- unparseable committee_id %r, storing NULL",
                               who, committee_raw)
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

    _load_donor_linked_csv(conn, cur, "key_accomplices.csv", "key_accomplices", _insert)


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


def _load_donor_linked_csv(conn: Any, cur: Any, csv_filename: str,
                           table: str, insert_row) -> None:
    """Match-or-create a donor for every row, upsert its image/address, then insert_row(cur, row, donor_id)."""
    from fec.database.leadership_matcher import (
        match_or_create_donor,
        upsert_donor_address,
        upsert_leader_employment,
    )

    csv_path = PROJECT_ROOT / "data" / "database" / csv_filename
    if not csv_path.exists():
        logger.info(f"  {csv_path.name} not found -- skipping {table}")
        return

    start = time.time()
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    match_counts: dict[str, int] = defaultdict(int)
    inserted = 0
    skipped = 0

    # leaders.csv prefixes person columns leader_*, key_accomplices.csv accomplice_*;
    # _col reads whichever is present so both files work through one loader.
    def _col(row, field):
        return (row.get(f"leader_{field}") or row.get(f"accomplice_{field}") or "").strip()

    for _, row in df.iterrows():
        name = _col(row, "name")
        if not name:
            skipped += 1
            continue

        first = _col(row, "first_name")
        last  = _col(row, "last_name")
        # Derive first/last from the "LAST, FIRST" name when the CSV leaves them blank.
        if (not first or not last) and "," in name:
            last_part, _, first_part = name.partition(",")
            last = last or last_part.strip()
            first = first or first_part.strip()
        city  = _col(row, "city")
        state = _col(row, "state")

        donor_id, method = match_or_create_donor(cur, name, first, last, city, state)
        match_counts[method] += 1

        # Portrait is a property of the donor (1:1), not the role.
        image_path = (row.get("image_path") or "").strip()
        if image_path:
            cur.execute(
                "INSERT INTO donor_images (donor_id, image_path) VALUES (%s, %s) "
                "ON CONFLICT (donor_id) DO UPDATE SET image_path = EXCLUDED.image_path",
                (donor_id, image_path),
            )

        street_1 = _col(row, "street_1")
        street_2 = _col(row, "street_2")
        if street_1 or city:
            # lat/lng parsed in SEPARATE guards so one bad value never nulls its
            # valid twin; blank is valid, non-blank garbage warns (hand-maintained files).
            lat_raw = (row.get("address_lat") or "").strip()
            lng_raw = (row.get("address_lng") or "").strip()
            latitude = longitude = None
            if lat_raw:
                try:
                    latitude = float(lat_raw)
                except ValueError:
                    logger.warning("  %s: %s -- unparseable address_lat %r, storing NULL",
                                   csv_filename, name, lat_raw)
            if lng_raw:
                try:
                    longitude = float(lng_raw)
                except ValueError:
                    logger.warning("  %s: %s -- unparseable address_lng %r, storing NULL",
                                   csv_filename, name, lng_raw)
            upsert_donor_address(
                cur, donor_id,
                street_1=street_1, street_2=street_2,
                city=city, state=state,
                zip_5=_col(row, "zip"),
                latitude=latitude, longitude=longitude,
            )

        # Employment ONLY from leaders.csv's leader_employer (a clean canonical
        # company). accomplice_employer is an editorial description; feeding it in
        # once inserted ~54 junk/duplicate employer rows that split companies in
        # v_company. Only fires for people who never donated; no-op when blank.
        upsert_leader_employment(cur, donor_id, (row.get("leader_employer") or "").strip())

        insert_row(cur, row, donor_id)
        inserted += 1

    conn.commit()
    _reset_id_sequence(conn, cur, table)
    logger.info(
        f"  {table}: {inserted} rows ({skipped} skipped), "
        f"match methods: {dict(match_counts)} ({time.time()-start:.1f}s)"
    )
