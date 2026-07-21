"""database/loader — load the cleaned FEC CSV into PostgreSQL.

    python -m fec.database.loader --reset|--nuke|--stats|--refresh|--init-roles

Split into _base (connection + helpers), schema (DDL), loading (data); this
package wires the CLI and re-exports the public API (connect, main).
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import pandas as pd

from fec.env import CLEANED_CSV, SCHEMA_SQL

from fec.log import get_logger

logger = get_logger(__name__)

from ._base import (
    PG,
    _count,
    connect,
    init_roles,
)
from .schema import (
    MAT_VIEWS,
    TABLES,
    VIEWS,
    create_schema,
    nuke_db,
    reset_schema,
)
from .loading import (
    load_all,
    load_key_accomplices,
    load_leadership,
    load_lookups,
    load_reference_tables,
    refresh_materialized_views,
)

__all__ = ["main", "connect", "init_roles", "TABLES", "VIEWS", "MAT_VIEWS"]


def show_stats(cur: Any) -> None:
    logger.info(f"\n  ── Database Stats ──")
    logger.info(f"  {'Name':35s} {'Type':8s} {'Rows':>10s}")
    logger.info(f"  {'─'*35} {'─'*8} {'─'*10}")
    for name in TABLES:
        count = _count(cur, name)
        logger.info(f"  {name:35s} {'table':8s} {count:>10,}")
    for name in VIEWS:
        count = _count(cur, name)
        logger.info(f"  {name:35s} {'view':8s} {count:>10,}")
    for name in MAT_VIEWS:
        count = _count(cur, name)
        logger.info(f"  {name:35s} {'matview':8s} {count:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load FEC data into normalized PostgreSQL")
    parser.add_argument("--reset", action="store_true", help="Drop all & recreate")
    parser.add_argument("--nuke", action="store_true", help="Drop & recreate DATABASE (needs POSTGRES_PASSWORD)")
    parser.add_argument("--stats", action="store_true", help="Show table counts")
    parser.add_argument("--init-roles", action="store_true", help="Create PG roles & DB")
    parser.add_argument("--refresh", action="store_true", help="Refresh materialized views")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    args = parser.parse_args()

    if args.init_roles:
        init_roles()
        return

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()
    dbname = PG["dbname"]

    logger.info(f"\n{'═'*60}")
    logger.info(f"  FEC Database v1.2 — Normalized Schema")
    logger.info(f"  Database: {dbname}")
    logger.info(f"{'═'*60}")

    if args.stats:
        show_stats(cur)
        conn.close()
        return

    if args.refresh:
        refresh_materialized_views(conn, cur)
        conn.close()
        logger.info(f"\n  ✓ Done")
        return

    if args.dry_run:
        logger.info(f"\n  [DRY RUN] Would:")
        logger.info(f"    1. Create schema ({SCHEMA_SQL})")
        logger.info(f"    3. Load donors (~17,700)")
        logger.info(f"    4. Load employers (~10,500)")
        logger.info(f"    5. Load donor_addresses (~20,000)")
        logger.info(f"    6. Load donor_employments (~22,000)")
        logger.info(f"    8. Load contributions (183,180)")
        logger.info(f"    9. Update employers with HQ addresses")
        logger.info(f"   11. Refresh materialized views")
        conn.close()
        return

    total_start = time.time()

    # Nuke = drop entire DB and recreate (clean slate)
    if args.nuke:
        conn.close()
        nuke_db()
        conn = connect()
        cur = conn.cursor()

    # Reset if requested (drop objects within existing DB)
    if args.reset or args.nuke:
        reset_schema(conn, cur)

    # Create schema (idempotent)
    create_schema(conn, cur)

    # Check for cleaned data
    if not CLEANED_CSV.exists():
        logger.info(f"\n  ✗ {CLEANED_CSV} not found")
        logger.info(f"    Run: python clean.py   (cleans + matches + adds donor_key)")
        conn.close()
        sys.exit(1)

    # Load cleaned data
    logger.info(f"\n── Reading {CLEANED_CSV.name} ──")
    # Project convention for cleaned files (same as build_employers.py): ONLY
    # a truly empty cell is NA — 'NULL' is a real surname, 'NAN' a real name.
    df = pd.read_csv(CLEANED_CSV, dtype=str, low_memory=False, keep_default_na=False,
                     na_values=[''])
    df['contribution_receipt_amount'] = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')

    if 'donor_key' not in df.columns:
        logger.error(f"✗ donor_key column not found")
        logger.info(f"    Run: python clean.py   (adds donor_key), or python donor_match.py --apply on an existing clean CSV")
        conn.close()
        sys.exit(1)

    logger.info(f"  {len(df):,} rows, {df['donor_key'].nunique():,} donors")

    load_lookups(conn, cur)
    load_all(conn, cur, df)

    # No contributions_cleaned load — the flat CSV layout can be
    # recomposed in code from the normalized tables if needed.

    # Reference tables (us_states, zip_centroids, key_accomplices, etc.)
    load_reference_tables(conn, cur)

    # Leadership and key_accomplices have their own loaders because each row
    # must be linked to a donor (via match-or-create) rather than bulk-inserted.
    load_leadership(conn, cur)
    load_key_accomplices(conn, cur)

    # Analyze
    logger.info(f"\n── Analyzing tables ──")
    for tbl in TABLES:
        if _count(cur, tbl) > 0:
            cur.execute(f"ANALYZE {tbl}")
    conn.commit()
    logger.info(f"  ✓ ANALYZE complete")

    # Materialized views
    refresh_materialized_views(conn, cur)

    # Grant read access — commit per grant so one failure's rollback can't
    # discard grants that already succeeded.
    logger.info(f"\n── Granting permissions ──")
    all_obj = TABLES + VIEWS + MAT_VIEWS
    granted = 0
    for name in all_obj:
        try:
            cur.execute(f"GRANT SELECT ON {name} TO fec_app")
            conn.commit()
            granted += 1
        except Exception as e:
            conn.rollback()
            logger.warning("  ⚠ GRANT SELECT on %s failed: %s",
                           name, str(e).strip().splitlines()[0] if str(e).strip() else e)
    logger.info(f"  ✓ fec_app: SELECT on {granted}/{len(all_obj)} objects")

    # Summary
    elapsed = time.time() - total_start
    m, s = divmod(int(elapsed), 60)

    logger.info(f"\n{'═'*60}")
    show_stats(cur)
    logger.info(f"\n  Total time: {m}m {s}s")
    logger.info(f"{'═'*60}")

    conn.close()


if __name__ == "__main__":
    main()
