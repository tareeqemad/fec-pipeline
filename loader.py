#!/usr/bin/env python3
"""Load cleaned FEC data into PostgreSQL."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

from fec.database.loader._base import connect, load_transaction, stage
from fec.database.loader.access import _check_reader, grant_read_access
from fec.database.loader.addresses import (
    link_employer_locations,
    load_address_dimension,
    load_donor_addresses,
)
from fec.database.loader.contributions import load_contributions
from fec.database.loader.donors import load_donors
from fec.database.loader.employers import _make_employer_resolver, load_employers, load_employments
from fec.database.loader.reference import load_lookups, load_reference_tables
from fec.database.loader.roster import (
    create_missing_donors,
    load_roster_employments,
    load_roster_tables,
    roster_companies,
    roster_jobs,
)
from fec.database.loader.schema_create import create_schema, verify_extensions
from fec.database.loader.schema_reset import reset_schema
from fec.database.loader.validate import read_input
from fec.env import CLEANED_CSV
from fec.log import get_logger

logger = get_logger("fec.database.loader.run")


# Map committee short names to database IDs.
def committee_lookup(cur: Any) -> dict[str, int]:
    cur.execute(
        "SELECT committee_short, committee_id FROM committees WHERE committee_short IS NOT NULL"
    )
    return {short: committee_id for short, committee_id in cur.fetchall()}


# Load tables in order and reuse their ID maps.
def load_all(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict],
    roster: list[dict],
) -> None:
    logger.info("[2/6] Resetting schema and loading lookups")
    with stage("schema"):
        reset_schema(cur)
        create_schema(cur)
    with stage("lookups"):
        occupation_category_ids = load_lookups(cur, df)
        committee_ids = committee_lookup(cur)
    jobs = roster_jobs(roster, df)

    logger.info("[3/6] Loading donors and employers")
    with stage("donors"):
        donor_ids = load_donors(cur, df)
        create_missing_donors(cur, roster, donor_ids)
    with stage("employers"):
        employer_ids = load_employers(cur, df, roster_companies(jobs))
    get_employer_id = _make_employer_resolver(employer_ids)

    logger.info("[4/6] Loading addresses, employments and contributions")
    with stage("addresses"):
        address_ids = load_address_dimension(cur, df, employer_locations, roster)
        donor_address_ids = load_donor_addresses(cur, df, donor_ids, address_ids, roster)
    with stage("employments"):
        employment_ids = load_employments(
            cur, df, donor_ids, occupation_category_ids, get_employer_id,
            address_ids, employer_locations,
        )
        load_roster_employments(
            cur, jobs, donor_ids, employer_ids, occupation_category_ids,
            address_ids, employer_locations,
        )
    with stage("contributions"):
        load_contributions(
            cur, df, donor_ids, committee_ids, donor_address_ids,
            employment_ids, get_employer_id, occupation_category_ids,
        )
    with stage("employer locations"):
        link_employer_locations(cur, address_ids, get_employer_id, employer_locations)

    logger.info("[5/6] Loading reference and roster data")
    with stage("reference tables"):
        load_reference_tables(cur)
    with stage("leaders and accomplices"):
        load_roster_tables(cur, roster, donor_ids, committee_ids)


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


# entry point: reset and reload the database from cleaned CSV
def main() -> None:
    total_start = time.perf_counter()
    _require_reset_flag()
    try:
        df, employer_locations, roster = read_input()
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
        load_all(cur, df, employer_locations, roster)
        logger.info("[6/6] Updating views and read access")
        with stage("statistics and views"):
            cur.execute("ANALYZE")
            # The view was created empty; refresh it with its unique index.
            cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
            cur.execute("ANALYZE mv_donor_profile")
        with stage("read access"):
            grant_read_access(cur)

    conn.close()
    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Database load committed.")
    logger.info("Input: %s rows | %s donor groups", f"{len(df):,}", f"{df['donor_key'].nunique():,}")
    logger.info("Completed in %dm %02ds.", minutes, seconds)


if __name__ == "__main__":
    main()
