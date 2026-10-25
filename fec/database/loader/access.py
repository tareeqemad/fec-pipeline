"""Give the read-only login SELECT on every table and view."""
from __future__ import annotations

from typing import Any

from fec.database.loader._base import _quote_identifier
from fec.env import (
    DATABASE_READER,
)


# Require a reader without admin rights or inherited roles.
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


# Give the reader SELECT access to tables and views.
def grant_read_access(cur: Any) -> None:
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
    except Exception as error:
        raise RuntimeError(f"Permission update failed: {error}") from error
