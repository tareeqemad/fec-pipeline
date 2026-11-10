"""Create the PostgreSQL schema from schema.sql."""

from __future__ import annotations

import os
from typing import Any

import sqlparse

from fec.env import SCHEMA_SQL


def create_schema(cur: Any) -> None:
    """Run schema.sql on the freshly reset database; any error ends the load."""
    for statement in sqlparse.split(SCHEMA_SQL.read_text(encoding="utf-8")):
        if sqlparse.format(statement, strip_comments=True).strip():
            cur.execute(statement)


def _database_name(cur: Any) -> str:
    """Read the database name for error messages."""
    connection = getattr(cur, "connection", None)
    return getattr(getattr(connection, "info", None), "dbname", None) or os.getenv("PG_DBNAME", "fec_db")


def verify_extensions(cur: Any) -> None:
    """Check required extensions before resetting tables."""
    required = {
        "pg_trgm": "fuzzy text search (gin_trgm_ops indexes)",
        "cube": "dependency of earthdistance",
        "earthdistance": "ll_to_earth() for map radius queries",
    }
    cur.execute("SELECT extname FROM pg_extension")
    present = {row[0] for row in cur.fetchall()}
    missing = sorted(set(required) - present)

    if not missing:
        return

    details = ", ".join(f"{extension} ({required[extension]})" for extension in missing)
    sql = " ".join(f"CREATE EXTENSION IF NOT EXISTS {extension};" for extension in missing)
    raise RuntimeError(
        f"Missing PostgreSQL extensions: {details}\n"
        "Run as a PostgreSQL superuser before loader.py --reset:\n"
        f'  sudo -u postgres psql -d {_database_name(cur)} -c "{sql}"'
    )
