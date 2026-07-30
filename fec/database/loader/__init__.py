"""Load the cleaned FEC CSV into PostgreSQL: python loader.py --first-run (new machine) or --reset (reload)."""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

import pandas as pd

from fec.env import CLEANED_CSV
from fec.log import get_logger

from ._base import PG, _count, connect, init_roles
from .addresses import link_employer_hqs, load_address_dimension, load_donor_addresses
from .contributions import load_contributions
from .donors import load_donors
from .employers import (
    _make_employer_resolver, link_previous_employers, load_employers, load_employments,
)
from .leadership import load_key_accomplices, load_leadership
from .reference import load_lookups, load_reference_tables
from .schema_create import MAT_VIEWS, TABLES, VIEWS, create_schema
from .schema_reset import reset_schema

logger = get_logger(__name__)

__all__ = ["main", "connect", "TABLES", "VIEWS", "MAT_VIEWS"]


def show_stats(cur: Any) -> None:
    logger.info("\n  -- Database Stats --")
    logger.info(f"  {'Name':35s} {'Type':8s} {'Rows':>10s}")
    logger.info(f"  {'-'*35} {'-'*8} {'-'*10}")
    for names, kind in ((TABLES, "table"), (VIEWS, "view"), (MAT_VIEWS, "matview")):
        for name in names:
            logger.info(f"  {name:35s} {kind:8s} {_count(cur, name):>10,}")


def load_all(conn: Any, cur: Any, df: pd.DataFrame) -> None:
    """Thread the 8 per-step loaders together, passing each step's id-maps along."""
    cur.execute("SELECT occupation_category_id, name FROM occupation_categories")
    occ_cat_map = {row[1]: row[0] for row in cur.fetchall()}

    # The cleaned CSV carries committee_short (AIPAC/DMFI/UDP), not the FEC number.
    cur.execute("SELECT committee_id, committee_short FROM committees WHERE committee_short IS NOT NULL")
    committee_map = {row[1]: row[0] for row in cur.fetchall()}

    donor_key_to_id = load_donors(conn, cur, df)
    emp_name_to_id = load_employers(conn, cur, df)
    # May grow emp_name_to_id in place -- must run before the resolver is built.
    donor_prev_employer_id = link_previous_employers(conn, cur, df, emp_name_to_id)
    get_employer_id = _make_employer_resolver(emp_name_to_id)

    addr_dim_id = load_address_dimension(conn, cur, df)
    addr_key_to_id = load_donor_addresses(conn, cur, df, donor_key_to_id, addr_dim_id)
    empl_donor_emp_to_id = load_employments(conn, cur, df, donor_key_to_id, occ_cat_map,
                                            donor_prev_employer_id, get_employer_id)
    load_contributions(conn, cur, df, donor_key_to_id, committee_map,
                       addr_key_to_id, empl_donor_emp_to_id, get_employer_id)

    # No denormalized "current" pointers and no cached first/last-seen dates:
    # the v_donor_* views derive both on read.

    link_employer_hqs(conn, cur, addr_dim_id, get_employer_id)


def refresh_materialized_views(conn: Any, cur: Any) -> None:
    """REFRESH mv_donor_profile CONCURRENTLY (schema.sql already created it plus the unique index CONCURRENTLY needs)."""
    logger.info("\n-- Refreshing materialized views --")
    start = time.time()

    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    conn.commit()
    cur.execute("ANALYZE mv_donor_profile")
    logger.info(f"  mv_donor_profile: {_count(cur, 'mv_donor_profile'):,} rows ({time.time()-start:.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load FEC data into normalized PostgreSQL")
    parser.add_argument("--first-run", action="store_true",
                        help="First time on a machine: create roles + database + extensions, then load (needs POSTGRES_PASSWORD)")
    parser.add_argument("--reset", action="store_true", help="Drop all objects & reload")
    args = parser.parse_args()

    if args.first_run == args.reset:
        parser.error("choose exactly one of --first-run / --reset")

    if args.first_run:
        init_roles()

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    logger.info(f"\n{'='*60}")
    logger.info("  FEC Database v1.2 -- Normalized Schema")
    logger.info(f"  Database: {PG['dbname']}")
    logger.info(f"{'='*60}")

    total_start = time.time()

    reset_schema(conn, cur)
    create_schema(conn, cur)

    if not CLEANED_CSV.exists():
        logger.info(f"\n  {CLEANED_CSV} not found")
        logger.info("    Run: python clean.py   (cleans + matches + adds donor_key)")
        conn.close()
        sys.exit(1)

    logger.info(f"\n-- Reading {CLEANED_CSV.name} --")
    # Project convention for cleaned files: ONLY a truly empty cell is NA --
    # 'NULL' is a real surname, 'NAN' a real name.
    df = pd.read_csv(CLEANED_CSV, dtype=str, low_memory=False, keep_default_na=False,
                     na_values=[''])
    df['contribution_receipt_amount'] = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')

    if 'donor_key' not in df.columns:
        logger.error("donor_key column not found")
        logger.info("    Run: python clean.py   (adds donor_key)")
        conn.close()
        sys.exit(1)

    logger.info(f"  {len(df):,} rows, {df['donor_key'].nunique():,} donors")

    load_lookups(conn, cur)
    load_all(conn, cur, df)

    load_reference_tables(conn, cur)

    # leaders / key_accomplices rows must each match-or-create a donor, so they
    # have their own loaders rather than the bulk-insert path.
    load_leadership(conn, cur)
    load_key_accomplices(conn, cur)

    logger.info("\n-- Analyzing tables --")
    for table in TABLES:
        if _count(cur, table) > 0:
            cur.execute(f"ANALYZE {table}")
    conn.commit()
    logger.info("  ANALYZE complete")

    refresh_materialized_views(conn, cur)

    # Commit per grant so one failure's rollback can't discard earlier grants.
    logger.info("\n-- Granting permissions --")
    all_objects = TABLES + VIEWS + MAT_VIEWS
    granted = 0
    for name in all_objects:
        try:
            cur.execute(f"GRANT SELECT ON {name} TO fec_app")
            conn.commit()
            granted += 1
        except Exception as error:
            conn.rollback()
            logger.warning("  GRANT SELECT on %s failed: %s",
                           name, str(error).strip().splitlines()[0] if str(error).strip() else error)
    logger.info(f"  fec_app: SELECT on {granted}/{len(all_objects)} objects")

    elapsed = time.time() - total_start
    minutes, seconds = divmod(int(elapsed), 60)

    logger.info(f"\n{'='*60}")
    show_stats(cur)
    logger.info(f"\n  Total time: {minutes}m {seconds}s")
    logger.info(f"{'='*60}")

    conn.close()


if __name__ == "__main__":
    main()
