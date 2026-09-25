"""Load leaders and key_accomplices from their hand-maintained CSVs.

Both rosters name committees by committee_short (leaders.committee_ids
'{AIPAC,DMFI}', key_accomplices.committee_id 'ZOA'); the loader resolves each
name through the committees table and refuses an unknown one.
"""
from __future__ import annotations

from typing import Any


try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.database.loader.people import _load_donor_linked_csv
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


