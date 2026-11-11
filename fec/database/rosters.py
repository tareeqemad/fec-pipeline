"""Load both rosters, resolving committee names to database IDs."""

from __future__ import annotations

from functools import partial
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.common.log import get_logger
from fec.database.reference_tables import read_committee_ids
from fec.database.roster_people import load_roster_csv

logger = get_logger(__name__)


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


def _validate_leader_committees(committees: dict[str, int], rows: pd.DataFrame) -> None:
    for _, row in rows.iterrows():
        leader_committee_ids(
            row.get("committee_ids"),
            committees,
            (row.get("leader_name") or "").strip(),
        )


def _insert_leader(committees: dict[str, int], cur: Any, row: pd.Series, donor_id: int) -> None:
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


def load_leadership(cur: Any) -> None:
    """Load leaders and their committee memberships."""
    committees = read_committee_ids(cur)
    load_roster_csv(
        cur,
        "leaders.csv",
        "leaders",
        partial(_insert_leader, committees),
        partial(_validate_leader_committees, committees),
    )


def _read_display_order(raw: str | None, who: str) -> int:
    text = (raw or "").strip()
    if not text:
        return 0
    if text.lstrip("-").isdigit():
        return int(text)
    logger.warning("  key_accomplices.csv: %s -- unparseable display_order %r, using 0", who, text)
    return 0


def _read_optional_text(row: pd.Series, column: str) -> str | None:
    value = (row.get(column) or "").strip()
    return value or None


def _validate_accomplice_committees(committees: dict[str, int], rows: pd.DataFrame) -> None:
    for _, row in rows.iterrows():
        accomplice_committee_id(
            row.get("committee_id"),
            committees,
            (row.get("accomplice_name") or "").strip(),
        )


def _insert_accomplice(committees: dict[str, int], cur: Any, row: pd.Series, donor_id: int) -> None:
    who = (row.get("accomplice_name") or "").strip()
    committee_id = accomplice_committee_id(row.get("committee_id"), committees, who)
    display_order = _read_display_order(row.get("display_order"), who)
    cur.execute(
        """
        INSERT INTO key_accomplices
            (donor_id, sign, subtitle, body_text,
             committee_id, display_order)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            donor_id,
            _read_optional_text(row, "sign"),
            _read_optional_text(row, "subtitle"),
            _read_optional_text(row, "body_text"),
            committee_id,
            display_order,
        ),
    )


def load_key_accomplices(cur: Any) -> None:
    """Load curated cards and link them to donors."""
    committees = read_committee_ids(cur)
    load_roster_csv(
        cur,
        "key_accomplices.csv",
        "key_accomplices",
        partial(_insert_accomplice, committees),
        partial(_validate_accomplice_committees, committees),
    )
