"""Create and verify the PostgreSQL schema."""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import sqlparse

from fec.database.loader.employers import EMPLOYMENT_KEY_COLUMNS
from fec.env import SCHEMA_SQL
from fec.log import get_logger

logger = get_logger(__name__)


TABLES = [
    "occupation_categories",
    "committees",
    "donors",
    "addresses",
    "employers",
    "donor_addresses",
    "donor_employments",
    "contributions",
    "us_states",
    "zcta_state_rel",
    "zip_centroids",
    "key_accomplices",
    "leaders",
    "leader_committees",
    "donor_images",
]


VIEWS = [
    "v_donor_current_address",
    "v_donor_current_employment",
    "v_donor_stats",
    "v_donor_profile",
]


MAT_VIEWS = ["mv_donor_profile"]


# Read SQL statements, skipping empty and comment-only text.
def _read_schema_statements() -> list[str]:
    if not SCHEMA_SQL.exists():
        logger.error(f"{SCHEMA_SQL} not found")
        sys.exit(1)

    sql = SCHEMA_SQL.read_text(encoding="utf-8")
    return [
        statement.strip()
        for statement in sqlparse.split(sql)
        if sqlparse.format(statement, strip_comments=True).strip()
    ]


# Run one SQL statement; skip objects that already exist.
def _execute_schema_statement(cur: Any, idx: int, statement: str) -> None:
    savepoint = f"sp_{idx}"
    try:
        cur.execute(f"SAVEPOINT {savepoint}")
        cur.execute(statement)
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
    except Exception as error:
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        if "already exists" in str(error):
            return

        first_line = str(error).splitlines()[0][:120]
        preview = statement[:200].replace("\n", " ")
        logger.error(f"Schema creation failed at statement #{idx + 1}: {first_line}")
        logger.error(f"  Statement preview: {preview}...")
        raise RuntimeError(
            f"Schema aborted at statement #{idx + 1}: {first_line}"
        ) from error


# Require all expected tables after creation.
def _verify_tables(cur: Any) -> None:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    actual = {row[0] for row in cur.fetchall()}
    missing = sorted(set(TABLES) - actual)
    if missing:
        raise RuntimeError(
            f"Schema creation completed but {len(missing)} expected table(s) "
            f"are missing: {missing}. Review schema.sql."
        )


# Create the schema and check its required objects.
def create_schema(cur: Any) -> None:
    statements = _read_schema_statements()
    for idx, statement in enumerate(statements):
        _execute_schema_statement(cur, idx, statement)

    _verify_tables(cur)
    _verify_schema_integrity(cur)


# Check the exact employment key, including NULLS NOT DISTINCT.
def _is_employment_key(definition: str) -> bool:
    match = re.fullmatch(r"UNIQUE NULLS NOT DISTINCT \((.*)\)", definition.strip())
    if not match:
        return False
    columns = tuple(column.strip() for column in match.group(1).split(","))
    return columns == EMPLOYMENT_KEY_COLUMNS


# Check the required views, relationships and employment key.
def _verify_schema_integrity(cur: Any) -> None:
    _require_names(cur, "SELECT viewname FROM pg_views WHERE schemaname='public'",
                   VIEWS, "views")
    _require_names(cur, "SELECT matviewname FROM pg_matviews WHERE schemaname='public'",
                   MAT_VIEWS, "materialized views")

    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='leader_committees'
        LIMIT 1
    """)
    if not cur.fetchone():
        raise RuntimeError("Schema: leader_committees junction table is missing")

    _verify_employments_table(cur)
    _reject_legacy_columns(cur)


# Reject missing names from a catalog query.
def _require_names(cur: Any, query: str, expected, label: str) -> None:
    cur.execute(query)
    actual = {row[0] for row in cur.fetchall()}
    missing = sorted(set(expected) - actual)
    if missing:
        raise RuntimeError(f"Schema: missing {label} {missing}")


# Check the employment key and previous self-employment column.
def _verify_employments_table(cur: Any) -> None:
    cur.execute("""
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'donor_employments'::regclass AND contype = 'u'
    """)
    unique_defs = [row[0] for row in cur.fetchall()]
    if not any(_is_employment_key(definition) for definition in unique_defs):
        raise RuntimeError(
            "Schema: donor_employments is missing the UNIQUE NULLS NOT DISTINCT "
            f"({', '.join(EMPLOYMENT_KEY_COLUMNS)}) constraint the loader "
            f"relies on (found: {unique_defs or 'no UNIQUE constraints'})"
        )

    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name='donor_employments'
          AND column_name='previous_self_employed'
    """)
    if not cur.fetchone():
        raise RuntimeError(
            "Schema: donor_employments.previous_self_employed is missing"
        )


# Reject old address and employment pointers on donors.
def _reject_legacy_columns(cur: Any) -> None:
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='donors'
          AND column_name IN ('current_address_id','current_employment_id','previous_employer_id')
    """)
    stale_cols = [row[0] for row in cur.fetchall()]
    if stale_cols:
        raise RuntimeError(
            f"Schema: donors still has legacy denormalized pointer columns "
            f"{stale_cols}. Drop them or reset_schema."
        )


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
