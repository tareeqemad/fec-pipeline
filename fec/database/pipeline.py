"""Load the cleaned FEC data into PostgreSQL in one transaction.

Run: python loader.py --reset

Steps, in order (main -> _reload_tables), all in one transaction:
    1. check_reader_role, verify_extensions
    2. reset_schema, create_schema (schema.sql), load_occupation_categories, load_committees
    3. load_donors, load_employers
    4. load_addresses, load_donor_addresses, load_employments, load_contributions, link_employer_offices
    5. load_reference_tables, load_leaders, load_key_accomplices
    6. refresh_donor_profiles, grant_read_access

Each loader of step 2 to 4 adds the IDs it created to the filings as a new column,
so that the next loader can use them.
"""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

from fec.common.log import get_logger
from fec.common.settings import CLEANED_CSV
from fec.database.addresses import link_employer_offices, load_addresses, load_donor_addresses
from fec.database.connection import connect, load_transaction
from fec.database.contributions import load_contributions
from fec.database.donors import load_donors
from fec.database.employments import load_employers, load_employments
from fec.database.input_checks import read_load_input
from fec.database.reference_tables import (
    committee_ids_by_short_name,
    load_committees,
    load_occupation_categories,
    load_reference_tables,
)
from fec.database.rosters import load_key_accomplices, load_leaders
from fec.database.schema import (
    check_reader_role,
    create_schema,
    grant_read_access,
    refresh_donor_profiles,
    reset_schema,
    verify_extensions,
)

# Keep the existing log-file category when moving the entry point.
logger = get_logger("fec.database.loader.run")


def _require_reset_flag() -> None:
    parser = argparse.ArgumentParser(description="Load FEC data into normalized PostgreSQL")
    parser.add_argument("--reset", action="store_true", help="Drop all objects & reload")
    if not parser.parse_args().reset:
        parser.error("use --reset to reload the database")


def _reload_tables(cur: Any, df: pd.DataFrame, offices: list[dict]) -> None:
    logger.info("[1/6] Checking database")
    check_reader_role(cur)
    verify_extensions(cur)

    logger.info("[2/6] Resetting schema and loading lookups")
    reset_schema(cur)
    create_schema(cur)
    load_occupation_categories(cur, df)
    load_committees(cur)
    df["committee_id"] = df["recipient_committee"].map(committee_ids_by_short_name(cur))

    logger.info("[3/6] Loading donors and employers")
    load_donors(cur, df)
    employer_ids = load_employers(cur, df)
    # The rosters may name other companies; the filings use only the offices of their own.
    filing_offices = [office for office in offices if office["employer_name"] in employer_ids]

    logger.info("[4/6] Loading addresses, employments and contributions")
    address_ids = load_addresses(cur, df, filing_offices)
    load_donor_addresses(cur, df)
    load_employments(cur, df, filing_offices, address_ids)
    load_contributions(cur, df)
    link_employer_offices(cur, filing_offices, employer_ids, address_ids)

    logger.info("[5/6] Loading reference and roster data")
    load_reference_tables(cur)
    load_leaders(cur, offices)
    load_key_accomplices(cur, offices)

    logger.info("[6/6] Updating views and read access")
    refresh_donor_profiles(cur)
    grant_read_access(cur)


def main() -> None:
    """Entry point: reset and reload the database from the cleaned CSV."""
    total_start = time.perf_counter()
    _require_reset_flag()
    try:
        df, offices = read_load_input()
    except (FileNotFoundError, ValueError) as error:
        raise SystemExit(f"ERROR: {error}") from error

    conn = connect()
    logger.info("Reading: %s (%s rows)", CLEANED_CSV.name, f"{len(df):,}")
    logger.info("Database: %s", conn.info.dbname)
    # One transaction from the drop to the grants; a failure keeps the old database.
    with load_transaction(conn):
        _reload_tables(conn.cursor(), df, offices)
    conn.close()

    minutes, seconds = divmod(round(time.perf_counter() - total_start), 60)
    logger.info("Database load committed.")
    logger.info("Input: %s rows | %s donor groups", f"{len(df):,}", f"{df['donor_key'].nunique():,}")
    logger.info("Completed in %dm %02ds.", minutes, seconds)
