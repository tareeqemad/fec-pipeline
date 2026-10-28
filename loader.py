#!/usr/bin/env python3
"""Load cleaned FEC data into PostgreSQL."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

from fec.database.loader._base import _count, connect, load_transaction, stage
from fec.database.loader.access import _check_reader, grant_read_access
from fec.database.loader.addresses import (
    link_employer_locations,
    load_address_dimension,
    load_donor_addresses,
)
from fec.database.loader.contributions import load_contributions
from fec.database.loader.donors import load_donors
from fec.database.loader.employers import (
    _make_employer_resolver,
    load_employers,
    load_employments,
)
from fec.database.loader.leadership import (
    committee_lookup,
    load_key_accomplices,
    load_leadership,
)
from fec.database.loader.previous_employers import link_previous_employers
from fec.database.loader.reference import load_lookups, load_reference_tables
from fec.database.loader.schema_create import (
    TABLES,
    create_schema,
    verify_extensions,
)
from fec.database.loader.schema_reset import reset_schema
from fec.database.loader.validate import _read_input
from fec.env import CLEANED_CSV
from fec.log import get_logger

# Keep the existing log-file category when moving the entry point.
logger = get_logger("fec.database.loader.run")

__all__ = ["main", "connect"]


# Load tables in order and reuse their ID maps.
def load_all(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict],
) -> None:
    cur.execute("SELECT occupation_category_id, name FROM occupation_categories")
    occupation_category_ids = {name: category_id for category_id, name in cur.fetchall()}

    committee_ids = committee_lookup(cur)

    logger.info("[3/6] Loading donors and employers")
    with stage("donors"):
        donor_ids = load_donors(cur, df)
    with stage("employers"):
        employer_ids = load_employers(cur, df)
        link_previous_employers(cur, df, employer_ids)
    get_employer_id = _make_employer_resolver(employer_ids)

    logger.info("[4/6] Loading addresses, employments and contributions")
    with stage("addresses"):
        address_ids = load_address_dimension(cur, df, employer_locations)
        donor_address_ids = load_donor_addresses(cur, df, donor_ids, address_ids)
    with stage("employments"):
        employment_ids = load_employments(
            cur, df, donor_ids, occupation_category_ids, get_employer_id,
            address_ids, employer_locations,
        )
    with stage("contributions"):
        load_contributions(
            cur, df, donor_ids, committee_ids, donor_address_ids,
            employment_ids, get_employer_id, occupation_category_ids,
        )
    with stage("employer locations"):
        link_employer_locations(cur, address_ids, get_employer_id, employer_locations)


# Refresh the donor view using its existing unique index.
def refresh_materialized_views(cur: Any) -> None:
    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    cur.execute("ANALYZE mv_donor_profile")


# Require --reset before reloading the database.
def _require_reset_flag() -> None:
    parser = argparse.ArgumentParser(
        description="Load FEC data into normalized PostgreSQL"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Drop all objects & reload"
    )
    args = parser.parse_args()

    if not args.reset:
        parser.error("use --reset to reload the database")


# Update PostgreSQL statistics for non-empty tables.
def _analyze_tables(cur: Any) -> None:
    for table in TABLES:
        if _count(cur, table) > 0:
            cur.execute(f"ANALYZE {table}")


# entry point: reset and reload the database from cleaned CSV
def main() -> None:
    total_start = time.perf_counter()
    _require_reset_flag()
    try:
        df, employer_locations = _read_input()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    logger.info("Reading: %s (%s rows)", CLEANED_CSV.name, f"{len(df):,}")
    logger.info("Database: %s", conn.info.dbname)
    logger.info("[1/6] Checking database")
    _check_reader(cur)
    verify_extensions(cur)

    # One transaction from drop to grants; failures keep the old.
    with load_transaction(conn):
        logger.info("[2/6] Resetting schema and loading lookups")
        with stage("schema"):
            reset_schema(cur)
            create_schema(cur)
        with stage("lookups"):
            load_lookups(cur)
        load_all(cur, df, employer_locations)
        logger.info("[5/6] Loading reference and roster data")
        with stage("reference tables"):
            load_reference_tables(cur)
        with stage("leaders and accomplices"):
            load_leadership(cur)
            load_key_accomplices(cur)
        logger.info("[6/6] Updating views and read access")
        with stage("statistics and views"):
            _analyze_tables(cur)
            refresh_materialized_views(cur)
        with stage("read access"):
            grant_read_access(cur)

    conn.close()
    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Database load committed.")
    logger.info("Input: %s rows | %s donor groups", f"{len(df):,}", f"{df['donor_key'].nunique():,}")
    logger.info("Completed in %dm %02ds.", minutes, seconds)


if __name__ == "__main__":
    main()
