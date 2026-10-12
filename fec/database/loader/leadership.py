"""Load leaders and key_accomplices from CSVs."""
from __future__ import annotations

from typing import Any

from psycopg2.extras import execute_values

from fec.database.loader.people import load_donor_linked_csv
from fec.log import get_logger

logger = get_logger(__name__)


# committee_short to id; never hard-code ids
def committee_lookup(cur: Any) -> dict[str, int]:
    cur.execute(
        "SELECT committee_short, committee_id FROM committees WHERE committee_short IS NOT NULL"
    )
    return {short: committee_id for short, committee_id in cur.fetchall()}


# committee_short to id; unknown stops load
def resolve_committee(
    short: str, committees: dict[str, int], csv_filename: str, who: str,
) -> int:
    committee_id = committees.get(short)
    if committee_id is None:
        raise ValueError(
            f"{csv_filename}: {who!r} names unknown committee {short!r}; "
            f"use one of {sorted(committees)} (committee_short in committees.csv)"
        )
    return committee_id


# parse committee_ids like '{AIPAC,DMFI}' into ids
def leader_committee_ids(
    raw: str | None, committees: dict[str, int], who: str,
) -> list[int]:
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


# parse accomplice committee_id; blank means none
def accomplice_committee_id(
    raw: str | None, committees: dict[str, int], who: str,
) -> int | None:
    short = (raw or "").strip()
    if not short:
        return None
    return resolve_committee(short, committees, "key_accomplices.csv", who)


# load leaders and leader_committees from CSV
def load_leadership(conn: Any, cur: Any) -> None:
    committees = committee_lookup(cur)

    # check all committee_ids before any insert
    def _validate(rows):
        for _, row in rows.iterrows():
            leader_committee_ids(
                row.get("committee_ids"), committees, (row.get("leader_name") or "").strip(),
            )

    # upsert leader, rebuild its committee links
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

        cur.execute("DELETE FROM leader_committees WHERE leader_id = %s", (leader_id,))
        if kept:
            execute_values(
                cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, committee_id) for committee_id in kept],
            )

    load_donor_linked_csv(conn, cur, "leaders.csv", "leaders", _insert, _validate)


# load key_accomplices cards from CSV
def load_key_accomplices(conn: Any, cur: Any) -> None:
    committees = committee_lookup(cur)

    # blank editorial field becomes None
    def _txt(row, col):
        value = (row.get(col) or "").strip()
        return value or None

    # check all committee_ids before any insert
    def _validate(rows):
        for _, row in rows.iterrows():
            accomplice_committee_id(
                row.get("committee_id"), committees, (row.get("accomplice_name") or "").strip(),
            )

    # upsert one key accomplice card
    def _insert(cur, row, donor_id):
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

    load_donor_linked_csv(conn, cur, "key_accomplices.csv", "key_accomplices", _insert, _validate)


