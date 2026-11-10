"""Load both rosters, resolving committee names to database IDs."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.common.log import get_logger
from fec.database.roster_people import _load_donor_linked_csv

logger = get_logger(__name__)


def committee_lookup(cur: Any) -> dict[str, int]:
    """Map committee short names to database IDs."""
    cur.execute("SELECT committee_short, committee_id FROM committees WHERE committee_short IS NOT NULL")
    return {short: committee_id for short, committee_id in cur.fetchall()}


def resolve_committee(
    short: str,
    committees: dict[str, int],
    csv_filename: str,
    who: str,
) -> int:
    """Find a committee ID or reject the unknown name."""
    committee_id = committees.get(short)
    if committee_id is None:
        raise ValueError(
            f"{csv_filename}: {who!r} names unknown committee {short!r}; "
            f"use one of {sorted(committees)} (committee_short in committees.csv)"
        )
    return committee_id


def leader_committee_ids(
    raw: str | None,
    committees: dict[str, int],
    who: str,
) -> list[int]:
    """Read the committee names listed for one leader."""
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
    raw: str | None,
    committees: dict[str, int],
    who: str,
) -> int | None:
    """Read the card committee, or None for a blank value."""
    short = (raw or "").strip()
    if not short:
        return None
    return resolve_committee(short, committees, "key_accomplices.csv", who)


def load_leadership(cur: Any) -> None:
    """Load leaders and their committee memberships."""
    committees = committee_lookup(cur)

    def validate_leader_committees(rows: pd.DataFrame) -> None:
        """Check every leader committee before inserting rows."""
        for _, row in rows.iterrows():
            leader_committee_ids(
                row.get("committee_ids"),
                committees,
                (row.get("leader_name") or "").strip(),
            )

    def insert_leader(cur: Any, row: pd.Series, donor_id: int) -> None:
        """Save the leader and replace its committee links."""
        committee_ids = leader_committee_ids(
            row.get("committee_ids"),
            committees,
            (row.get("leader_name") or "").strip(),
        )

        cur.execute("INSERT INTO leaders (donor_id) VALUES (%s) RETURNING leader_id", (donor_id,))
        leader_id = cur.fetchone()[0]
        if committee_ids:
            execute_values(
                cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, committee_id) for committee_id in committee_ids],
            )

    _load_donor_linked_csv(
        cur,
        "leaders.csv",
        "leaders",
        insert_leader,
        validate_leader_committees,
    )


def _accomplice_display_order(raw: str | None, who: str) -> int:
    """Read display order, using zero for missing or invalid text."""
    text = (raw or "").strip()
    if not text:
        return 0
    if text.lstrip("-").isdigit():
        return int(text)
    logger.warning("  key_accomplices.csv: %s -- unparseable display_order %r, using 0", who, text)
    return 0


def load_key_accomplices(cur: Any) -> None:
    """Load curated cards and link them to donors."""
    committees = committee_lookup(cur)

    def optional_text(row: pd.Series, column: str) -> str | None:
        """Return trimmed text or None."""
        value = (row.get(column) or "").strip()
        return value or None

    def validate_accomplice_committees(rows: pd.DataFrame) -> None:
        """Check every card committee before inserting rows."""
        for _, row in rows.iterrows():
            accomplice_committee_id(
                row.get("committee_id"),
                committees,
                (row.get("accomplice_name") or "").strip(),
            )

    def insert_accomplice(cur: Any, row: pd.Series, donor_id: int) -> None:
        """Save the card text, committee and display order."""
        who = (row.get("accomplice_name") or "").strip()
        committee_id = accomplice_committee_id(row.get("committee_id"), committees, who)
        display_order = _accomplice_display_order(row.get("display_order"), who)
        cur.execute(
            """
            INSERT INTO key_accomplices
                (donor_id, sign, subtitle, body_text,
                 committee_id, display_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                donor_id,
                optional_text(row, "sign"),
                optional_text(row, "subtitle"),
                optional_text(row, "body_text"),
                committee_id,
                display_order,
            ),
        )

    _load_donor_linked_csv(
        cur,
        "key_accomplices.csv",
        "key_accomplices",
        insert_accomplice,
        validate_accomplice_committees,
    )
