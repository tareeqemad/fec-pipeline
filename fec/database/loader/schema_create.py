"""Create and verify the PostgreSQL schema."""

from __future__ import annotations

import sys
from collections import Counter
from typing import Any

import sqlparse

from fec.env import SCHEMA_SQL
from fec.log import get_logger

from ._base import PG

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
    # Reference tables
    "us_states",
    "zcta_state_rel",
    "zip_centroids",
    "key_accomplices",
    "leaders",
    "leader_committees",
    "donor_images",
]


VIEWS = [
    # Donor profile helpers.
    "v_donor_current_address",
    "v_donor_current_employment",
    "v_donor_newest_address",  # Leader addresses.
    "v_donor_newest_employment",  # Leader employment.
    # Donor aggregates.
    "v_donor_stats",
    # Donor profile source.
    "v_donor_profile",
    # Contribution history.
    "v_contributions_cleaned",
    # Dashboard views.
    "v_leaders",
    "v_key_accomplices",
    # Company identities.
    "v_company",
]


MAT_VIEWS = ["mv_donor_profile"]


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


def _schema_object_kind(statement: str) -> str:
    upper = statement.upper()
    if "CREATE TABLE" in upper:
        return "tables"
    if "CREATE INDEX" in upper or "CREATE UNIQUE INDEX" in upper:
        return "indexes"
    if "CREATE VIEW" in upper or "MATERIALIZED VIEW" in upper:
        return "views"
    if (
        "CREATE FUNCTION" in upper
        or "CREATE OR REPLACE FUNCTION" in upper
        or "CREATE TRIGGER" in upper
    ):
        return "functions"
    return ""


def _execute_schema_statement(conn: Any, cur: Any, idx: int, statement: str) -> bool:
    savepoint = f"sp_{idx}"
    try:
        cur.execute(f"SAVEPOINT {savepoint}")
        cur.execute(statement)
        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
        return True
    except Exception as error:
        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        if "already exists" in str(error):
            return False

        first_line = str(error).splitlines()[0][:120]
        preview = statement[:200].replace("\n", " ")
        logger.error(f"Schema creation failed at statement #{idx + 1}: {first_line}")
        logger.error(f"  Statement preview: {preview}...")
        conn.rollback()
        raise RuntimeError(
            f"Schema aborted at statement #{idx + 1}: {first_line}"
        ) from error


def _verify_tables(cur: Any) -> None:
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    actual = {row[0] for row in cur.fetchall()}
    missing = sorted(set(TABLES) - actual)
    if missing:
        raise RuntimeError(
            f"Schema creation completed but {len(missing)} expected table(s) "
            f"are missing: {missing}. Review schema.sql."
        )
    logger.info(f"  all {len(TABLES)} expected tables present")


def create_schema(conn: Any, cur: Any) -> None:
    """Create and verify the database schema."""
    logger.info(f"\n-- Creating schema from {SCHEMA_SQL.name} --")
    statements = _read_schema_statements()
    counts = Counter()
    for idx, statement in enumerate(statements):
        if _execute_schema_statement(conn, cur, idx, statement):
            kind = _schema_object_kind(statement)
            if kind:
                counts[kind] += 1

    conn.commit()
    logger.info(
        f"  {counts['tables']} tables, {counts['indexes']} indexes, "
        f"{counts['views']} views, {counts['functions']} functions"
    )

    _verify_tables(cur)
    _verify_schema_integrity(cur)


def _verify_schema_integrity(cur: Any) -> None:
    """Assert v1.2 invariants: views/matviews exist, leader_committees junction, donor_employments unique key, no legacy donor pointer columns."""
    cur.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    actual_views = {row[0] for row in cur.fetchall()}
    missing_views = sorted(set(VIEWS) - actual_views)
    if missing_views:
        raise RuntimeError(f"Schema: missing views {missing_views}")

    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname='public'")
    actual_mvs = {row[0] for row in cur.fetchall()}
    missing_mvs = sorted(set(MAT_VIEWS) - actual_mvs)
    if missing_mvs:
        raise RuntimeError(f"Schema: missing materialized views {missing_mvs}")

    # Verify leader relationships.
    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='leader_committees'
        LIMIT 1
    """)
    if not cur.fetchone():
        raise RuntimeError("Schema: leader_committees junction table is missing")

    # Verify employment uniqueness.
    cur.execute("""
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'donor_employments'::regclass AND contype = 'u'
    """)
    unique_defs = [row[0] for row in cur.fetchall()]
    if not any(
        "NULLS NOT DISTINCT" in definition
        and "donor_id" in definition
        and "employer_id" in definition
        and "occupation" in definition
        for definition in unique_defs
    ):
        raise RuntimeError(
            "Schema: donor_employments is missing the UNIQUE NULLS NOT DISTINCT "
            "(donor_id, employer_id, occupation) constraint declared in "
            f"schema.sql (found: {unique_defs or 'no UNIQUE constraints'})"
        )

    # Reject legacy columns.
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

    logger.info(
        f"  schema v1.2 integrity checks pass "
        f"(views={len(VIEWS)}, mvs={len(MAT_VIEWS)}, constraints OK)"
    )


def verify_extensions(cur: Any) -> None:
    """Require extensions before destructive reset."""
    required = {
        "pg_trgm": "fuzzy text search (gin_trgm_ops indexes)",
        "cube": "dependency of earthdistance",
        "earthdistance": "ll_to_earth() for map radius queries",
    }
    cur.execute("SELECT extname FROM pg_extension")
    present = {row[0] for row in cur.fetchall()}
    missing = sorted(set(required) - present)

    if not missing:
        logger.info(
            f"  all {len(required)} required extensions present "
            f"({', '.join(sorted(required))})"
        )
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
        f'  sudo -u postgres psql -d {PG["dbname"]} -c "{sql}"'
    )
