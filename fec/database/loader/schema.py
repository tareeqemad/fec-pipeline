"""database/loader/schema.py — schema DDL — create / reset / nuke + integrity checks + table registries."""
from __future__ import annotations

import sys
from typing import Any

import sqlparse

try:
    import psycopg2
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import get_env, SCHEMA_SQL

from fec.log import get_logger

logger = get_logger(__name__)

from ._base import PG


def nuke_db() -> None:
    """Drop and recreate the database from scratch using the postgres superuser.
    Kills all active connections, drops the DB, recreates it with proper ownership."""
    pg_pass = get_env("POSTGRES_PASSWORD", required=True)
    db = PG["dbname"]
    owner = PG["user"]

    conn = psycopg2.connect(
        host=PG["host"], port=PG["port"],
        dbname="postgres", user="postgres", password=pg_pass,
    )
    conn.autocommit = True
    cur = conn.cursor()

    # Kill active connections
    cur.execute("""
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = %s AND pid <> pg_backend_pid()
    """, (db,))
    logger.info(f"  Terminated active connections to {db}")

    # Drop and recreate
    cur.execute(f"DROP DATABASE IF EXISTS {db}")
    cur.execute(f"CREATE DATABASE {db} OWNER {owner}")
    logger.info(f"  ✓ Database {db} recreated (owner: {owner})")

    conn.close()

    # Grant schema permissions + create extensions (needs superuser)
    conn2 = psycopg2.connect(
        host=PG["host"], port=PG["port"],
        dbname=db, user="postgres", password=pg_pass,
    )
    conn2.autocommit = True
    cur2 = conn2.cursor()
    cur2.execute(f"GRANT ALL ON SCHEMA public TO {owner}")
    for ext in ('pg_trgm', 'cube', 'earthdistance'):
        try:
            cur2.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
        except Exception as e:
            logger.warning(f"  ⚠ Could not create extension {ext}: {e}")
            conn2.rollback()
    # Grant default privileges so fec_app can create tables/views
    cur2.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {owner}")
    cur2.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {owner}")
    conn2.close()
    logger.info(f"  ✓ Granted ALL on schema public to {owner}")
    logger.info(f"  ✓ Extensions created (pg_trgm, cube, earthdistance)")


def reset_schema(conn: Any, cur: Any) -> None:
    """Drop all tables/views/matviews in public schema.

    The previous implementation had 3 bugs:
      1. A hand-coded drop list: missed anything not in the list, and
         printed a misleading "dropped N" count equal to the list length
         regardless of what actually got dropped.
      2. Shared transaction with bare rollback: when one ALTER/DROP failed
         (e.g. permission denied on a table owned by another role), the
         bare `conn.rollback()` rolled back every previous successful drop
         in the same transaction. A whole "reset" could silently leave
         most objects intact.
      3. No verification: create_schema would then run against leftover
         objects, CREATE TABLE IF NOT EXISTS would silently skip, and
         downstream ON CONFLICT failures surfaced only much later.

    New flow:
      - Query pg_tables / pg_views / pg_matviews for what actually exists.
      - Use SAVEPOINTs so a single drop failure doesn't roll back the rest.
      - For objects not owned by us, try ALTER OWNER first (needed for DROP).
      - Commit at the end, then re-query: if anything remains, raise.
    """
    logger.info(f"\n  ⚠ Dropping all objects in public schema...")

    cur.execute("SELECT current_user")
    current_user = cur.fetchone()[0]

    # Discover what's actually there — don't rely on a static list.
    cur.execute("SELECT matviewname, matviewowner FROM pg_matviews WHERE schemaname = 'public'")
    matviews = cur.fetchall()
    cur.execute("SELECT viewname,     viewowner    FROM pg_views    WHERE schemaname = 'public'")
    views = cur.fetchall()
    cur.execute("SELECT tablename,    tableowner   FROM pg_tables   WHERE schemaname = 'public'")
    tables = cur.fetchall()
    # Order matters for FKs, but CASCADE handles them — still prefer
    # matviews → views → tables so error messages are predictable.
    objects = (
        [(n, 'MATERIALIZED VIEW', o) for n, o in matviews]
        + [(n, 'VIEW', o)            for n, o in views]
        + [(n, 'TABLE', o)           for n, o in tables]
    )

    if not objects:
        logger.info("  ✓ Already empty (nothing to drop)")
        return

    dropped = 0
    failed: list[tuple[str, str, str, str]] = []  # (name, kind, owner, reason)

    for i, (name, kind, owner) in enumerate(objects):
        # The object list is a snapshot taken before the loop, but each DROP
        # below is CASCADE — so dropping one object can take its dependent
        # views with it. By the time the loop reaches such a view it is
        # already gone, and ALTER OWNER (unlike DROP, which is IF EXISTS)
        # would fail with "relation does not exist". That is a SUCCESS, not a
        # failure: the object is gone, which is all reset_schema wants.
        cur.execute("SELECT to_regclass(%s)", (f'public."{name}"',))
        if cur.fetchone()[0] is None:
            dropped += 1
            continue

        # If we don't own it, DROP will fail — try ALTER OWNER first.
        if owner != current_user:
            try:
                cur.execute(f"SAVEPOINT sp_alt_{i}")
                cur.execute(f'ALTER {kind} "{name}" OWNER TO {current_user}')
                cur.execute(f"RELEASE SAVEPOINT sp_alt_{i}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT sp_alt_{i}")
                failed.append((name, kind, owner, f'ALTER OWNER: {str(e).splitlines()[0][:80]}'))
                continue

        try:
            cur.execute(f"SAVEPOINT sp_drop_{i}")
            cur.execute(f'DROP {kind} IF EXISTS "{name}" CASCADE')
            cur.execute(f"RELEASE SAVEPOINT sp_drop_{i}")
            dropped += 1
        except Exception as e:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_drop_{i}")
            failed.append((name, kind, owner, f'DROP: {str(e).splitlines()[0][:80]}'))

    conn.commit()
    logger.info(f"  ✓ Dropped {dropped}/{len(objects)} objects")

    if failed:
        logger.error(f"  ✗ {len(failed)} object(s) could NOT be dropped:")
        for name, kind, owner, reason in failed[:10]:
            logger.error(f"    - {kind} {name} (owner={owner}) — {reason}")
        if len(failed) > 10:
            logger.error(f"    ... and {len(failed) - 10} more")
        raise RuntimeError(
            f"reset_schema: {len(failed)} object(s) could not be dropped "
            f"(likely owned by another role). Fix options:\n"
            f"  1. Run as superuser: sudo -u postgres psql -d {PG['dbname']} "
            f"-c \"REASSIGN OWNED BY <old_owner> TO {current_user}\"\n"
            f"  2. Use --nuke instead (requires postgres superuser + pg_hba TCP access)\n"
            f"  3. Manually DROP the listed objects as their owner"
        )

    # Post-drop verification: nothing should remain in public schema.
    cur.execute("""
        SELECT 'TABLE' AS kind, tablename AS name FROM pg_tables   WHERE schemaname='public'
        UNION ALL
        SELECT 'VIEW',          viewname              FROM pg_views    WHERE schemaname='public'
        UNION ALL
        SELECT 'MATVIEW',       matviewname           FROM pg_matviews WHERE schemaname='public'
    """)
    leftover = cur.fetchall()
    if leftover:
        raise RuntimeError(
            f"reset_schema: drops reported success but {len(leftover)} object(s) remain: "
            f"{[f'{k} {n}' for k, n in leftover[:5]]}. "
            f"Check for objects created outside the reset path."
        )


def create_schema(conn: Any, cur: Any) -> None:
    """Create tables, indexes, views from schema.sql.

    Uses sqlparse to split the SQL file correctly. The previous hand-rolled
    parser split on ';' and toggled on '$$', which silently mis-parsed
    anything more complex (multi-line CREATE TABLE with inline comments,
    triggers, string literals containing ';', etc.) — leaving a partial
    schema that later caused opaque "no unique constraint" ON CONFLICT
    failures deep in the load.

    Strategy now:
      1. sqlparse.split() handles all SQL edge cases correctly.
      2. SAVEPOINT is kept so "already exists" can be tolerated (idempotent
         re-runs without --nuke).
      3. Any OTHER error aborts immediately with a clear message — a partial
         schema is worse than no schema; do not cascade into loading.
      4. Sanity check at the end: every table in TABLES must exist in
         pg_tables. If any is missing, raise.
    """
    logger.info(f"\n── Creating schema from {SCHEMA_SQL.name} ──")

    if not SCHEMA_SQL.exists():
        logger.error(f"✗ {SCHEMA_SQL} not found")
        sys.exit(1)

    sql = SCHEMA_SQL.read_text()

    # sqlparse.split() groups leading comments with their following statement,
    # so "-- Donors\nCREATE TABLE donors(...)" comes back as one chunk. Filter
    # out chunks that are PURE comments / whitespace (no executable SQL) —
    # psycopg2 raises "can't execute an empty query" on those otherwise.
    def _has_sql(chunk: str) -> bool:
        return bool(sqlparse.format(chunk, strip_comments=True).strip())

    statements = [s.strip() for s in sqlparse.split(sql) if _has_sql(s)]

    tables = views = indexes = funcs = 0
    for i, stmt in enumerate(statements):
        try:
            cur.execute(f"SAVEPOINT sp_{i}")
            cur.execute(stmt)
            cur.execute(f"RELEASE SAVEPOINT sp_{i}")
            upper = stmt.upper()
            if 'CREATE TABLE' in upper:
                tables += 1
            elif 'CREATE INDEX' in upper or 'CREATE UNIQUE INDEX' in upper:
                indexes += 1
            elif 'CREATE VIEW' in upper or 'MATERIALIZED VIEW' in upper:
                views += 1
            elif ('CREATE FUNCTION' in upper
                  or 'CREATE OR REPLACE FUNCTION' in upper
                  or 'CREATE TRIGGER' in upper):
                funcs += 1
        except Exception as e:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_{i}")
            if 'already exists' in str(e):
                continue
            # STOP — do not continue with a partial schema.
            first_line = str(e).splitlines()[0][:120]
            preview = stmt[:200].replace('\n', ' ')
            logger.error(f"✗ Schema creation failed at statement #{i + 1}: {first_line}")
            logger.error(f"  Statement preview: {preview}...")
            conn.rollback()
            raise RuntimeError(
                f"Schema aborted at statement #{i + 1}: {first_line}"
            ) from e

    conn.commit()
    logger.info(f"  ✓ {tables} tables, {indexes} indexes, {views} views, {funcs} functions")

    # Sanity check: verify every expected table actually exists. If the
    # parser or a DDL statement silently misbehaved, this catches it before
    # we waste an hour loading into a half-built schema.
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    actual = {r[0] for r in cur.fetchall()}
    missing = sorted(set(TABLES) - actual)
    if missing:
        raise RuntimeError(
            f"Schema creation completed but {len(missing)} expected table(s) "
            f"are missing: {missing}. Review schema.sql."
        )
    logger.info(f"  ✓ all {len(TABLES)} expected tables present")

    _verify_extensions(conn, cur)
    _verify_schema_integrity(conn, cur)


def _verify_schema_integrity(conn: Any, cur: Any) -> None:
    """Schema-level invariants that must hold for the v1.2 schema.

    Cheap catches for regressions that wouldn't fail CREATE but would
    break the data model:
      - expected views + materialized views exist
      - the leader_committees junction exists (the real M:N for leader↔committee)
      - donor_employments has a NULLS NOT DISTINCT unique key for
        donor/employer/occupation identity
      - the denormalized pointers the schema dropped are NOT present on
        donors (avoid silent resurrection from an old schema file)
    """
    # Views
    cur.execute("SELECT viewname FROM pg_views WHERE schemaname='public'")
    actual_views = {r[0] for r in cur.fetchall()}
    missing_views = sorted(set(VIEWS) - actual_views)
    if missing_views:
        raise RuntimeError(f"Schema: missing views {missing_views}")

    # Materialized views
    cur.execute("SELECT matviewname FROM pg_matviews WHERE schemaname='public'")
    actual_mvs = {r[0] for r in cur.fetchall()}
    missing_mvs = sorted(set(MAT_VIEWS) - actual_mvs)
    if missing_mvs:
        raise RuntimeError(f"Schema: missing materialized views {missing_mvs}")

    # leader↔committee is a real M:N via the leader_committees junction. Assert it exists.
    cur.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema='public' AND table_name='leader_committees'
        LIMIT 1
    """)
    if not cur.fetchone():
        raise RuntimeError(
            "Schema: leader_committees junction table is missing"
        )

    # donor_employments must carry the UNIQUE NULLS NOT DISTINCT dedup key on
    # (donor_id, employer_id, occupation) — schema.sql declares it, and both
    # the loader (groupby + seen-set feed rows that rely on it as the DB-level
    # backstop) and leadership_matcher's plain INSERT count on it existing.
    cur.execute("""
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'donor_employments'::regclass AND contype = 'u'
    """)
    unique_defs = [r[0] for r in cur.fetchall()]
    if not any('NULLS NOT DISTINCT' in d and 'donor_id' in d
               and 'employer_id' in d and 'occupation' in d for d in unique_defs):
        raise RuntimeError(
            "Schema: donor_employments is missing the UNIQUE NULLS NOT DISTINCT "
            "(donor_id, employer_id, occupation) constraint declared in "
            f"schema.sql (found: {unique_defs or 'no UNIQUE constraints'})"
        )

    # Ensure legacy denormalized pointers are NOT present
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema='public' AND table_name='donors'
          AND column_name IN ('current_address_id','current_employment_id','previous_employer_id')
    """)
    stale_cols = [r[0] for r in cur.fetchall()]
    if stale_cols:
        raise RuntimeError(
            f"Schema: donors still has legacy denormalized pointer columns "
            f"{stale_cols}. Drop them or reset_schema."
        )

    logger.info(f"  ✓ schema v1.2 integrity checks pass "
                f"(views={len(VIEWS)}, mvs={len(MAT_VIEWS)}, constraints OK)")


def _verify_extensions(conn: Any, cur: Any) -> None:
    """Ensure required PostgreSQL extensions are installed.

    nuke_db() creates these as the postgres superuser, but --reset uses
    the regular app role and can't see through to extension state. If
    earthdistance is missing the map's /api/map-donors endpoint fails
    with "function ll_to_earth(unknown, unknown) does not exist" —
    obscure unless you know what you're looking for. So: check, try to
    install, and if install fails (typical — CREATE EXTENSION needs
    superuser) emit the exact command the user needs to run.
    """
    required = {
        'pg_trgm':       'fuzzy text search (gin_trgm_ops indexes)',
        'cube':          'dependency of earthdistance',
        'earthdistance': 'll_to_earth() for map radius queries',
    }
    cur.execute("SELECT extname FROM pg_extension")
    present = {r[0] for r in cur.fetchall()}
    missing = {ext: why for ext, why in required.items() if ext not in present}

    if not missing:
        logger.info(f"  ✓ all {len(required)} required extensions present "
                    f"({', '.join(sorted(required))})")
        return

    # Try to create — works only if the session is a superuser.
    still_missing: list[tuple[str, str, str]] = []  # (ext, reason, error)
    for ext, why in missing.items():
        try:
            cur.execute(f"SAVEPOINT sp_ext_{ext}")
            cur.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            cur.execute(f"RELEASE SAVEPOINT sp_ext_{ext}")
            logger.info(f"  ✓ installed extension {ext}")
        except Exception as e:
            cur.execute(f"ROLLBACK TO SAVEPOINT sp_ext_{ext}")
            still_missing.append((ext, why, str(e).splitlines()[0][:120]))

    conn.commit()

    if still_missing:
        dbname = PG["dbname"]
        lines = [
            f"Required PostgreSQL extension(s) are missing and the current "
            f"role can't install them:",
        ]
        for ext, why, err in still_missing:
            lines.append(f"  - {ext:<15} ({why})")
            lines.append(f"      {err}")
        lines.append("")
        lines.append("Fix — run one of these as a superuser:")
        lines.append(f"  sudo -u postgres psql -d {dbname} -c \"CREATE EXTENSION "
                     f"{', '.join(ext for ext, _, _ in still_missing)}\"")
        lines.append(f"  # or, if postgres is not an OS user with socket access:")
        lines.append(f"  psql -U <superuser> -d {dbname} "
                     f"-c \"CREATE EXTENSION {', '.join(ext for ext, _, _ in still_missing)}\"")
        raise RuntimeError("\n".join(lines))


TABLES = [
    "occupation_categories", "committees",
    "donors", "addresses", "employers", "donor_addresses", "donor_employments",
    "contributions",
    # Reference tables
    "us_states", "zcta_state_rel", "zip_centroids",
    "key_accomplices", "leaders", "leader_committees", "donor_images",
]


VIEWS = [
    # Sub-views (latest per donor) — feed v_donor_profile
    "v_donor_current_address", "v_donor_current_employment",
    "v_donor_newest_address",     # used by leaders / key_accomplices views
    "v_donor_newest_employment",  # used by v_leaders (covers non-donor leaders)
    # Aggregate view — feeds v_donor_profile
    "v_donor_stats",
    # Composed view — source for mv_donor_profile
    "v_donor_profile",
    # Flat per-contribution view — web app donor-profile history reads this
    "v_contributions_cleaned",
    # Dashboard views (flatten leaders / key_accomplices for the app)
    "v_leaders",
    "v_key_accomplices",
    # Unified company view — employer firm + its own donation as one identity
    "v_company",
]


MAT_VIEWS = ["mv_donor_profile"]
