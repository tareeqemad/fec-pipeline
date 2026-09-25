"""Give the read-only login SELECT on every table and view."""
from __future__ import annotations

from typing import Any

from fec.database.loader._base import _quote_identifier
from fec.env import (
    DATABASE_READER,
)
from fec.log import get_logger

logger = get_logger(__name__)


# verify the reader role exists and is unprivileged, non-inheriting
def _check_reader(cur: Any) -> None:
    cur.execute(
        """
        SELECT
            rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls,
            EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE member = pg_roles.oid
            )
        FROM pg_roles
        WHERE rolname = %s
        """,
        (DATABASE_READER,),
    )
    role = cur.fetchone()

    if role is None:
        raise RuntimeError(f"Database role {DATABASE_READER} does not exist")
    if role[0]:
        raise RuntimeError(f"{DATABASE_READER} must be unprivileged")
    if role[1]:
        raise RuntimeError(f"{DATABASE_READER} must not inherit another role")


# give the read-only role SELECT on tables and views only
def grant_read_access(conn: Any, cur: Any) -> None:
    """Give fec_app SELECT on every table, view and materialized view, nothing else."""
    logger.info("\n-- Granting permissions --")
    _check_reader(cur)

    reader = _quote_identifier(DATABASE_READER)

    statements = (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {reader}",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {reader}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {reader}",
    )

    try:
        for statement in statements:
            cur.execute(statement)
        conn.commit()
    except Exception as error:
        conn.rollback()
        raise RuntimeError(f"Permission update failed: {error}") from error

    # ALL TABLES covers tables, views and materialized views
    logger.info("  %s: SELECT on tables, views and materialized views; nothing else", DATABASE_READER)
