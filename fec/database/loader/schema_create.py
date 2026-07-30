"""Expected schema objects, plus create_schema and its table/view/constraint/extension verification."""
from __future__ import annotations

import sys
from typing import Any

import sqlparse

from fec.env import SCHEMA_SQL
from fec.log import get_logger

from ._base import PG

logger = get_logger(__name__)


TABLES = [
    "occupation_categories", "committees",
    "donors", "addresses", "employers", "donor_addresses", "donor_employments",
    "contributions",
    # Reference tables
    "us_states", "zcta_state_rel", "zip_centroids",
    "key_accomplices", "leaders", "leader_committees", "donor_images",
]


VIEWS = [
    # Latest-per-donor sub-views feeding v_donor_profile
    "v_donor_current_address", "v_donor_current_employment",
    "v_donor_newest_address",     # used by leaders / key_accomplices views
    "v_donor_newest_employment",  # used by v_leaders (covers non-donor leaders)
    # Aggregate view feeding v_donor_profile
    "v_donor_stats",
    # Composed view, source for mv_donor_profile
    "v_donor_profile",
    # Flat per-contribution view; web app donor-profile history reads this
    "v_contributions_cleaned",
    # Dashboard views (flatten leaders / key_accomplices for the app)
    "v_leaders",
    "v_key_accomplices",
    # Unified company view: employer firm + its own donation as one identity
    "v_company",
]


MAT_VIEWS = ["mv_donor_profile"]


def create_schema(conn: Any, cur: Any) -> None:
    """Create tables/indexes/views from schema.sql; tolerates 'already exists', aborts on any other error (a partial schema is worse than none)."""
    logger.info(f"\n-- Creating schema from {SCHEMA_SQL.name} --")

    if not SCHEMA_SQL.exists():
        logger.error(f"{SCHEMA_SQL} not found")
        sys.exit(1)

    # explicit encoding: the schema is UTF-8, and Windows' default codepage would mojibake it
    sql = SCHEMA_SQL.read_text(encoding="utf-8")

    # sqlparse.split() groups leading comments with their statement; drop chunks
    # with no executable SQL -- psycopg2 raises "can't execute an empty query".
    def _has_sql(chunk: str) -> bool:
        return bool(sqlparse.format(chunk, strip_comments=True).strip())

    statements = [statement.strip() for statement in sqlparse.split(sql) if _has_sql(statement)]

    tables = views = indexes = functions = 0
    for idx, statement in enumerate(statements):
        try:
            cur.execute(f"SAVEPOINT sp_{idx}")
            cur.execute(statement)
            cur.execute(f"RELEASE SAVEPOINT sp_{idx}")
            upper = statement.upper()
            if 'CREATE TABLE' in upper:
                tables += 1
            elif 'CREATE INDEX' in upper or 'CREATE UNIQUE INDEX' in upper:
                indexes += 1
            elif 'CREATE VIEW' in upper or 'MATERIALIZED VIEW' in upper:
                views += 1
            elif ('CREATE FUNCTION' in upper
                  or 'CREATE OR REPLACE FUNCTION' in upper
                  or 'CREATE TRIGGER' in upper):
                functions += 1
        except Exception as error:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_{idx}")
            if 'already exists' in str(error):
                continue
            # STOP -- do not continue with a partial schema.
            first_line = str(error).splitlines()[0][:120]
            preview = statement[:200].replace('\n', ' ')
            logger.error(f"Schema creation failed at statement #{idx + 1}: {first_line}")
            logger.error(f"  Statement preview: {preview}...")
            conn.rollback()
            raise RuntimeError(
                f"Schema aborted at statement #{idx + 1}: {first_line}"
            ) from error

    conn.commit()
    logger.info(f"  {tables} tables, {indexes} indexes, {views} views, {functions} functions")

    # Catch silent parser/DDL misbehavior before loading into a half-built schema.
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    actual = {row[0] for row in cur.fetchall()}
    missing = sorted(set(TABLES) - actual)
    if missing:
        raise RuntimeError(
            f"Schema creation completed but {len(missing)} expected table(s) "
            f"are missing: {missing}. Review schema.sql."
        )
    logger.info(f"  all {len(TABLES)} expected tables present")

    _verify_extensions(conn, cur)
    _verify_schema_integrity(conn, cur)


def _verify_schema_integrity(conn: Any, cur: Any) -> None:
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

    # leader/committee is a real M:N via the leader_committees junction.
    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='leader_committees'
        LIMIT 1
    """)
    if not cur.fetchone():
        raise RuntimeError(
            "Schema: leader_committees junction table is missing"
        )

    # Both the loader's dedup and leadership_matcher's plain INSERT count on this
    # UNIQUE NULLS NOT DISTINCT key as the DB-level backstop.
    cur.execute("""
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'donor_employments'::regclass AND contype = 'u'
    """)
    unique_defs = [row[0] for row in cur.fetchall()]
    if not any('NULLS NOT DISTINCT' in definition and 'donor_id' in definition
               and 'employer_id' in definition and 'occupation' in definition
               for definition in unique_defs):
        raise RuntimeError(
            "Schema: donor_employments is missing the UNIQUE NULLS NOT DISTINCT "
            "(donor_id, employer_id, occupation) constraint declared in "
            f"schema.sql (found: {unique_defs or 'no UNIQUE constraints'})"
        )

    # Legacy denormalized pointers must NOT resurrect from an old schema file.
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

    logger.info(f"  schema v1.2 integrity checks pass "
                f"(views={len(VIEWS)}, mvs={len(MAT_VIEWS)}, constraints OK)")


def _verify_extensions(conn: Any, cur: Any) -> None:
    """Check the required extensions, try to install missing ones, and on failure print the exact superuser command."""
    required = {
        'pg_trgm':       'fuzzy text search (gin_trgm_ops indexes)',
        'cube':          'dependency of earthdistance',
        'earthdistance': 'll_to_earth() for map radius queries',
    }
    cur.execute("SELECT extname FROM pg_extension")
    present = {row[0] for row in cur.fetchall()}
    missing = {extension: why for extension, why in required.items() if extension not in present}

    if not missing:
        logger.info(f"  all {len(required)} required extensions present "
                    f"({', '.join(sorted(required))})")
        return

    # CREATE EXTENSION works only for a superuser session.
    still_missing: list[tuple[str, str, str]] = []  # (ext, reason, error)
    for extension, why in missing.items():
        try:
            cur.execute(f"SAVEPOINT sp_ext_{extension}")
            cur.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
            cur.execute(f"RELEASE SAVEPOINT sp_ext_{extension}")
            logger.info(f"  installed extension {extension}")
        except Exception as error:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_ext_{extension}")
            still_missing.append((extension, why, str(error).splitlines()[0][:120]))

    conn.commit()

    if still_missing:
        dbname = PG["dbname"]
        lines = [
            "Required PostgreSQL extension(s) are missing and the current "
            "role can't install them:",
        ]
        for extension, why, error in still_missing:
            lines.append(f"  - {extension:<15} ({why})")
            lines.append(f"      {error}")
        lines.append("")
        lines.append("Fix -- run one of these as a superuser:")
        lines.append(f"  sudo -u postgres psql -d {dbname} -c \"CREATE EXTENSION "
                     f"{', '.join(extension for extension, _, _ in still_missing)}\"")
        lines.append("  # or, if postgres is not an OS user with socket access:")
        lines.append(f"  psql -U <superuser> -d {dbname} "
                     f"-c \"CREATE EXTENSION {', '.join(extension for extension, _, _ in still_missing)}\"")
        raise RuntimeError("\n".join(lines))
