"""Load the cleaned FEC data into PostgreSQL in one transaction."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV
from fec.database.addresses import (
    link_employer_offices,
    load_address_dimension,
    load_donor_addresses,
)
from fec.database.connection import connect, load_transaction, logged_stage, to_native
from fec.database.contributions import load_contributions
from fec.database.employments import load_employers, load_employments, make_employer_lookup
from fec.database.input_checks import read_load_input
from fec.database.previous_employers import link_previous_employers
from fec.database.read_access import check_reader_role, grant_read_access
from fec.database.reference_tables import load_lookups, load_reference_tables
from fec.database.rosters import committee_ids_by_short_name, load_key_accomplices, load_leadership
from fec.database.schema import create_schema, reset_schema, verify_extensions


def load_donors(cur: Any, df: pd.DataFrame) -> dict:
    """Load one donor per donor_key and return its ID."""
    donor_rows = []
    for donor_key, group in df.groupby("donor_key"):
        # Names are already cleaned.
        latest = group.sort_values("contribution_receipt_date", ascending=False).iloc[0]
        entity = to_native(latest["entity_type"])
        first = to_native(latest["contributor_first_name"])
        last = to_native(latest["contributor_last_name"])
        # Organizations use last_name.
        if not last and entity in ("COMMITTEE/PAC", "ORGANIZATION"):
            last = to_native(latest["contributor_name"])
        # Older CSVs default to confirmed.
        status = to_native(latest.get("identity_status")) or "confirmed"
        donor_rows.append((donor_key, entity, first, last, status))

    execute_values(
        cur,
        "INSERT INTO donors (donor_key, entity_type, first_name, last_name, identity_status) "
        "VALUES %s",
        donor_rows,
        page_size=5000,
    )

    cur.execute("SELECT donor_id, donor_key FROM donors")
    return {donor_key: donor_id for donor_id, donor_key in cur.fetchall()}


# Keep the existing log-file category when moving the entry point.
logger = get_logger("fec.database.loader.run")

__all__ = ["main", "connect"]


def load_filing_tables(
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict],
) -> None:
    """Load tables in order and reuse their ID maps."""
    # Each loaded table adds its ID map here for the tables that link to it.
    id_maps: dict[str, dict] = {}

    cur.execute("SELECT occupation_category_id, name FROM occupation_categories")
    id_maps["occupation_category"] = {name: category_id for category_id, name in cur.fetchall()}

    id_maps["committee"] = committee_ids_by_short_name(cur)

    logger.info("[3/6] Loading donors and employers")
    with logged_stage("donors"):
        id_maps["donor"] = load_donors(cur, df)
    with logged_stage("employers"):
        employer_ids = load_employers(cur, df)
        link_previous_employers(cur, df, employer_ids)
    get_employer_id = make_employer_lookup(employer_ids)

    logger.info("[4/6] Loading addresses, employments and contributions")
    with logged_stage("addresses"):
        id_maps["address"] = load_address_dimension(cur, df, employer_locations)
        id_maps["donor_address"] = load_donor_addresses(
            cur, df, id_maps["donor"], id_maps["address"]
        )
    with logged_stage("employments"):
        id_maps["employment"] = load_employments(
            cur, df, id_maps, get_employer_id, employer_locations
        )
    with logged_stage("contributions"):
        load_contributions(cur, df, id_maps, get_employer_id)
    with logged_stage("employer locations"):
        link_employer_offices(cur, id_maps["address"], get_employer_id, employer_locations)


def refresh_materialized_views(cur: Any) -> None:
    """Refresh the donor view using its existing unique index."""
    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    cur.execute("ANALYZE mv_donor_profile")


def _require_reset_flag() -> None:
    """Require --reset before reloading the database."""
    parser = argparse.ArgumentParser(description="Load FEC data into normalized PostgreSQL")
    parser.add_argument("--reset", action="store_true", help="Drop all objects & reload")
    args = parser.parse_args()

    if not args.reset:
        parser.error("use --reset to reload the database")


def _reload_tables(cur: Any, df: pd.DataFrame, employer_locations: list[dict]) -> None:
    """Rebuild the schema, load every table and grant read access."""
    logger.info("[2/6] Resetting schema and loading lookups")
    with logged_stage("schema"):
        reset_schema(cur)
        create_schema(cur)
    with logged_stage("lookups"):
        load_lookups(cur)
    load_filing_tables(cur, df, employer_locations)
    logger.info("[5/6] Loading reference and roster data")
    with logged_stage("reference tables"):
        load_reference_tables(cur)
    with logged_stage("leaders and accomplices"):
        load_leadership(cur)
        load_key_accomplices(cur)
    logger.info("[6/6] Updating views and read access")
    with logged_stage("statistics and views"):
        cur.execute("ANALYZE")
        refresh_materialized_views(cur)
    with logged_stage("read access"):
        grant_read_access(cur)


def main() -> None:
    """Entry point: reset and reload the database from cleaned CSV."""
    total_start = time.perf_counter()
    _require_reset_flag()
    try:
        df, employer_locations = read_load_input()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    logger.info("Reading: %s (%s rows)", CLEANED_CSV.name, f"{len(df):,}")
    logger.info("Database: %s", conn.info.dbname)
    logger.info("[1/6] Checking database")
    check_reader_role(cur)
    verify_extensions(cur)

    # One transaction from drop to grants; failures keep the old.
    with load_transaction(conn):
        _reload_tables(cur, df, employer_locations)

    conn.close()
    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Database load committed.")
    logger.info(
        "Input: %s rows | %s donor groups", f"{len(df):,}", f"{df['donor_key'].nunique():,}"
    )
    logger.info("Completed in %dm %02ds.", minutes, seconds)
