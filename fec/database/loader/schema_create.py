"""Create the PostgreSQL schema from schema.sql."""

from __future__ import annotations

import os
from typing import Any

import sqlparse

from fec.env import SCHEMA_SQL


# Read SQL statements, skipping empty and comment-only text.
def _read_schema_statements() -> list[str]:
    if not SCHEMA_SQL.exists():
        raise RuntimeError(f"{SCHEMA_SQL} not found")
    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    return [
        statement.strip()
        for statement in sqlparse.split(sql)
        if sqlparse.format(statement, strip_comments=True).strip()
    ]


# Run schema.sql statement by statement; the schema was just reset.
def create_schema(cur: Any) -> None:
    for number, statement in enumerate(_read_schema_statements(), start=1):
        try:
            cur.execute(statement)
        except Exception as error:
            first_line = str(error).splitlines()[0][:120]
            preview = statement[:200].replace("\n", " ")
            raise RuntimeError(
                f"Schema aborted at statement #{number}: {first_line}\n  {preview}..."
            ) from error


# Read the database name for error messages.
def _database_name(cur: Any) -> str:
    connection = getattr(cur, "connection", None)
    return getattr(getattr(connection, "info", None), "dbname", None) or os.getenv("PG_DBNAME", "fec_db")


# Check required extensions before resetting tables.
def verify_extensions(cur: Any) -> None:
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

    details = ", ".join(
        f"{extension} ({required[extension]})" for extension in missing
    )
    sql = " ".join(
        f"CREATE EXTENSION IF NOT EXISTS {extension};" for extension in missing
    )
    raise RuntimeError(
        f"Missing PostgreSQL extensions: {details}\n"
        "Run as a PostgreSQL superuser before loader.py --reset:\n"
        f'  sudo -u postgres psql -d {_database_name(cur)} -c "{sql}"'
    )
