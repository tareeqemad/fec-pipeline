"""Prepare the database: check roles and extensions, drop the old tables, create the schema, grant read access."""

from __future__ import annotations

from typing import Any

from psycopg2.extensions import quote_ident

from fec.common.settings import DATABASE_OWNER, DATABASE_READER, SCHEMA_SQL

# pg_trgm for name search; cube and earthdistance for map radius queries.
REQUIRED_EXTENSIONS = ["pg_trgm", "cube", "earthdistance"]

OBJECTS_SQL = """
    SELECT matviewname, 'MATERIALIZED VIEW', matviewowner
    FROM pg_matviews WHERE schemaname = 'public'
    UNION ALL
    SELECT viewname, 'VIEW', viewowner FROM pg_views WHERE schemaname = 'public'
    UNION ALL
    SELECT tablename, 'TABLE', tableowner FROM pg_tables WHERE schemaname = 'public'
"""


def check_reader_role(cur: Any) -> None:
    """Require a reader login without admin rights or inherited roles."""
    cur.execute(
        """
        SELECT
            rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls,
            EXISTS (SELECT 1 FROM pg_auth_members WHERE member = pg_roles.oid)
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


def verify_extensions(cur: Any) -> None:
    """Require the extensions the schema uses; only a superuser can add them."""
    cur.execute("SELECT extname FROM pg_extension")
    present = {row[0] for row in cur.fetchall()}
    missing = [extension for extension in REQUIRED_EXTENSIONS if extension not in present]
    if missing:
        sql = " ".join(f"CREATE EXTENSION IF NOT EXISTS {extension};" for extension in missing)
        raise RuntimeError(
            f"Missing PostgreSQL extensions: {', '.join(missing)}\n"
            "Run as a PostgreSQL superuser before loader.py --reset:\n"
            f'  sudo -u postgres psql -d {cur.connection.info.dbname} -c "{sql}"'
        )


def reset_schema(cur: Any) -> None:
    """Drop every public table and view, after checking the loader owns them all."""
    cur.execute("SELECT current_user")
    current_user = cur.fetchone()[0]
    if current_user != DATABASE_OWNER:
        raise RuntimeError(f"Loader must run as {DATABASE_OWNER}, not {current_user}. Set PG_USER={DATABASE_OWNER}.")

    cur.execute(OBJECTS_SQL)
    objects = cur.fetchall()
    wrongly_owned = [f"{kind} {name} ({owner})" for name, kind, owner in objects if owner != DATABASE_OWNER]
    if wrongly_owned:
        raise RuntimeError(f"Ownership check failed. Expected {DATABASE_OWNER}: {', '.join(wrongly_owned[:5])}")

    for name, kind, _ in objects:
        cur.execute(f"DROP {kind} IF EXISTS {quote_ident(name, cur)} CASCADE")


def create_schema(cur: Any) -> None:
    """Run schema.sql on the emptied database."""
    cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))


def refresh_donor_profiles(cur: Any) -> None:
    """Update the table statistics and fill the cached donor profiles."""
    cur.execute("ANALYZE")
    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    cur.execute("ANALYZE mv_donor_profile")


def grant_read_access(cur: Any) -> None:
    """Leave the reader login with SELECT on every table and view, and nothing else."""
    for statement in (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {DATABASE_READER}",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {DATABASE_READER}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {DATABASE_READER}",
    ):
        cur.execute(statement)
