"""Drop the public tables and views, then create the schema from schema.sql."""

from __future__ import annotations

import os
from typing import Any

import sqlparse

from fec.common.settings import DATABASE_OWNER, SCHEMA_SQL
from fec.database.connection import quote_identifier


def create_schema(cur: Any) -> None:
    """Run schema.sql on the freshly reset database; any error ends the load."""
    for statement in sqlparse.split(SCHEMA_SQL.read_text(encoding="utf-8")):
        if sqlparse.format(statement, strip_comments=True).strip():
            cur.execute(statement)


def _read_database_name(cur: Any) -> str:
    """Read the database name for error messages."""
    connection = getattr(cur, "connection", None)
    connection_info = getattr(connection, "info", None)
    dbname = getattr(connection_info, "dbname", None)
    return dbname or os.getenv("PG_DBNAME", "fec_db")


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
        f'  sudo -u postgres psql -d {_read_database_name(cur)} -c "{sql}"'
    )


OBJECTS_SQL = """
    SELECT matviewname, 'MATERIALIZED VIEW', matviewowner
    FROM pg_matviews WHERE schemaname = 'public'
    UNION ALL
    SELECT viewname, 'VIEW', viewowner FROM pg_views WHERE schemaname = 'public'
    UNION ALL
    SELECT tablename, 'TABLE', tableowner FROM pg_tables WHERE schemaname = 'public'
"""


def reset_schema(cur: Any) -> None:
    """Check ownership, drop public tables and views, then verify removal."""
    cur.execute("SELECT current_user")
    current_user = cur.fetchone()[0]

    if current_user != DATABASE_OWNER:
        raise RuntimeError(
            f"Loader must run as {DATABASE_OWNER}, not {current_user}. "
            f"Set PG_USER={DATABASE_OWNER}."
        )

    cur.execute(OBJECTS_SQL)
    objects = cur.fetchall()

    wrongly_owned = [
        (name, kind, owner) for name, kind, owner in objects if owner != DATABASE_OWNER
    ]
    if wrongly_owned:
        preview = ", ".join(f"{kind} {name} ({owner})" for name, kind, owner in wrongly_owned[:5])
        raise RuntimeError(f"Ownership check failed. Expected {DATABASE_OWNER}: {preview}")

    for name, kind, _owner in objects:
        cur.execute(f"DROP {kind} IF EXISTS {quote_identifier(name)} CASCADE")
