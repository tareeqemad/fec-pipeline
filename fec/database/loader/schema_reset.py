"""Drop every object in the public schema so the loader can rebuild from scratch."""
from __future__ import annotations

from typing import Any

from fec.env import DATABASE_OWNER
from fec.log import get_logger

logger = get_logger(__name__)

OBJECTS_SQL = """
    SELECT matviewname, 'MATERIALIZED VIEW', matviewowner
    FROM pg_matviews WHERE schemaname = 'public'
    UNION ALL
    SELECT viewname, 'VIEW', viewowner FROM pg_views WHERE schemaname = 'public'
    UNION ALL
    SELECT tablename, 'TABLE', tableowner FROM pg_tables WHERE schemaname = 'public'
"""


# drop all owner-controlled objects, verify schema ends empty
def reset_schema(conn: Any, cur: Any) -> None:
    """Drop owner-controlled objects and verify public is empty."""
    logger.info("\n  Dropping all objects in public schema...")

    cur.execute("SELECT current_user")
    current_user = cur.fetchone()[0]

    if current_user != DATABASE_OWNER:
        raise RuntimeError(
            f"Loader must run as {DATABASE_OWNER}, not {current_user}. "
            f"Set PG_USER={DATABASE_OWNER}."
        )

    cur.execute(OBJECTS_SQL)
    objects = cur.fetchall()

    wrong = [
        (name, kind, owner)
        for name, kind, owner in objects
        if owner != DATABASE_OWNER
    ]
    if wrong:
        preview = ", ".join(
            f"{kind} {name} ({owner})"
            for name, kind, owner in wrong[:5]
        )
        raise RuntimeError(
            "Ownership check failed. "
            f"Expected {DATABASE_OWNER}: {preview}"
        )

    if not objects:
        logger.info("  Already empty")
        return

    try:
        for name, kind, _owner in objects:
            quoted = name.replace('"', '""')
            cur.execute(f'DROP {kind} IF EXISTS "{quoted}" CASCADE')

        cur.execute(OBJECTS_SQL)
        leftover = cur.fetchall()
        if leftover:
            names = ", ".join(f"{kind} {name}" for name, kind, _ in leftover[:5])
            raise RuntimeError(f"Schema reset left objects: {names}")
    except Exception as error:
        conn.rollback()
        raise RuntimeError(f"Schema reset failed: {error}") from error

    logger.info("  Dropped %s objects", len(objects))
