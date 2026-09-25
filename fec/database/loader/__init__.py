"""Load cleaned FEC data."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

from fec.database.loader.access import _check_reader, grant_read_access
from fec.database.loader.validate import _read_input
from fec.env import CLEANED_CSV
from fec.log import get_logger

from ._base import PG, _count, connect
from .addresses import (
    link_employer_locations,
    load_address_dimension,
    load_donor_addresses,
)
from .contributions import load_contributions
from .donors import load_donors
from .employers import _make_employer_resolver, load_employers, load_employments
from .leadership import load_key_accomplices, load_leadership
from .previous_employers import link_previous_employers, previous_self_employed_donors
from .reference import load_lookups, load_reference_tables
from .schema_create import MAT_VIEWS, TABLES, VIEWS, create_schema, verify_extensions
from .schema_reset import reset_schema

logger = get_logger(__name__)

__all__ = ["main", "connect"]


# log row counts for every table, view, and matview
def show_stats(cur: Any) -> None:
    logger.info("\n  -- Database Stats --")
    logger.info(f"  {'Name':35s} {'Type':8s} {'Rows':>10s}")
    logger.info(f"  {'-' * 35} {'-' * 8} {'-' * 10}")
    for names, kind in ((TABLES, "table"), (VIEWS, "view"), (MAT_VIEWS, "matview")):
        for name in names:
            logger.info(f"  {name:35s} {kind:8s} {_count(cur, name):>10,}")


# load every table in dependency order, passing ID maps forward
def load_all(
    conn: Any,
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict],
) -> None:
    """Load tables and pass their ID maps forward."""
    cur.execute("SELECT occupation_category_id, name FROM occupation_categories")
    occ_cat_map = {row[1]: row[0] for row in cur.fetchall()}

    cur.execute(
        "SELECT committee_id, committee_short FROM committees WHERE committee_short IS NOT NULL"
    )
    committee_map = {row[1]: row[0] for row in cur.fetchall()}

    donor_key_to_id = load_donors(conn, cur, df)
    emp_name_to_id = load_employers(conn, cur, df)
    donor_prev_employer_id = link_previous_employers(conn, cur, df, emp_name_to_id)
    prev_self_employed = previous_self_employed_donors(df)
    get_employer_id = _make_employer_resolver(emp_name_to_id)

    addr_dim_id = load_address_dimension(conn, cur, df, employer_locations)
    addr_key_to_id = load_donor_addresses(conn, cur, df, donor_key_to_id, addr_dim_id)
    empl_donor_emp_to_id = load_employments(
        conn,
        cur,
        df,
        donor_key_to_id,
        occ_cat_map,
        donor_prev_employer_id,
        get_employer_id,
        addr_dim_id,
        employer_locations,
        prev_self_employed,
    )
    load_contributions(
        conn,
        cur,
        df,
        donor_key_to_id,
        committee_map,
        addr_key_to_id,
        empl_donor_emp_to_id,
        get_employer_id,
    )

    link_employer_locations(
        conn,
        cur,
        addr_dim_id,
        get_employer_id,
        employer_locations,
    )


# refresh materialized views concurrently
def refresh_materialized_views(conn: Any, cur: Any) -> None:
    """Refresh concurrently; schema.sql supplies the required unique index."""
    logger.info("\n-- Refreshing materialized views --")
    start = time.time()

    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    conn.commit()
    cur.execute("ANALYZE mv_donor_profile")
    logger.info(
        f"  mv_donor_profile: {_count(cur, 'mv_donor_profile'):,} rows ({time.time() - start:.1f}s)"
    )


# stop unless the caller passed --reset
def _require_reset_flag() -> None:
    """Stop unless the caller passed --reset."""
    parser = argparse.ArgumentParser(
        description="Load FEC data into normalized PostgreSQL"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Drop all objects & reload"
    )
    args = parser.parse_args()

    if not args.reset:
        parser.error("use --reset to reload the database")


# refresh planner statistics for every non-empty table
def _analyze_tables(conn, cur) -> None:
    """Refresh planner statistics for every non-empty table."""
    logger.info("\n-- Analyzing tables --")
    for table in TABLES:
        if _count(cur, table) > 0:
            cur.execute(f"ANALYZE {table}")
    conn.commit()
    logger.info("  ANALYZE complete")


# entry point: reset and reload the database from cleaned CSV
def main() -> None:
    _require_reset_flag()
    try:
        df, employer_locations = _read_input()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Database v1.2 -- Normalized Schema")
    logger.info(f"  Database: {PG['dbname']}")
    logger.info(f"{'=' * 60}")

    _check_reader(cur)
    verify_extensions(cur)

    total_start = time.time()

    logger.info(f"\n-- Reading {CLEANED_CSV.name} --")
    logger.info(f"  {len(df):,} rows, {df['donor_key'].nunique():,} donors")

    reset_schema(conn, cur)
    create_schema(conn, cur)

    load_lookups(conn, cur)
    load_all(conn, cur, df, employer_locations)

    load_reference_tables(conn, cur)

    load_leadership(conn, cur)
    load_key_accomplices(conn, cur)

    _analyze_tables(conn, cur)

    refresh_materialized_views(conn, cur)

    grant_read_access(conn, cur)

    elapsed = time.time() - total_start
    minutes, seconds = divmod(int(elapsed), 60)

    logger.info(f"\n{'=' * 60}")
    show_stats(cur)
    logger.info(f"\n  Total time: {minutes}m {seconds}s")
    logger.info(f"{'=' * 60}")

    conn.close()


if __name__ == "__main__":
    main()
