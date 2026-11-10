"""Drop public tables and views before reloading."""

from __future__ import annotations

from typing import Any

from fec.database.loader._base import _quote_identifier
from fec.env import DATABASE_OWNER

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
        raise RuntimeError(f"Loader must run as {DATABASE_OWNER}, not {current_user}. Set PG_USER={DATABASE_OWNER}.")

    cur.execute(OBJECTS_SQL)
    objects = cur.fetchall()

    wrong = [(name, kind, owner) for name, kind, owner in objects if owner != DATABASE_OWNER]
    if wrong:
        preview = ", ".join(f"{kind} {name} ({owner})" for name, kind, owner in wrong[:5])
        raise RuntimeError(f"Ownership check failed. Expected {DATABASE_OWNER}: {preview}")

    for name, kind, _owner in objects:
        cur.execute(f"DROP {kind} IF EXISTS {_quote_identifier(name)} CASCADE")
