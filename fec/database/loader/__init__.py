"""Load cleaned FEC data."""

from __future__ import annotations

import argparse
import time
from typing import Any

import pandas as pd

from fec.cleaning.previous_employer import referenced_employers
from fec.cleaning.quality import run_quality_gates
from fec.config.data import FINAL_OUTPUT_COLUMNS
from fec.env import (
    CLEANED_CSV,
    COMMITTEES_CSV,
    DATABASE_READER,
    EMPLOYER_LOCATIONS_CSV,
)
from fec.io import read_pipeline_csv
from fec.log import get_logger
from fec.resolve.pipeline.locations import ADDRESS_TRUST_VALUES

from ._base import PG, _count, _quote_identifier, connect
from .addresses import (
    link_employer_locations,
    load_address_dimension,
    load_donor_addresses,
    load_employer_locations,
)
from .contributions import load_contributions
from .donors import load_donors
from .employers import (
    _make_employer_resolver,
    link_previous_employers,
    load_employers,
    load_employments,
    previous_self_employed_donors,
)
from .leadership import load_key_accomplices, load_leadership
from .reference import load_lookups, load_reference_tables
from .schema_create import MAT_VIEWS, TABLES, VIEWS, create_schema, verify_extensions
from .schema_reset import reset_schema

logger = get_logger(__name__)

__all__ = ["main", "connect"]

_REQUIRED_COLUMNS = set(FINAL_OUTPUT_COLUMNS)
_LOCATION_COLUMNS = {
    "employer_name",
    "employer_address",
    "employer_city",
    "employer_state",
    "employer_zip",
    "employer_latitude",
    "employer_longitude",
    "is_primary",
    "address_source",
    "address_trust",
}


def _validate_cleaned_data(df: pd.DataFrame) -> None:
    missing = sorted(_REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise ValueError(f"{CLEANED_CSV.name}: missing columns: {', '.join(missing)}")

    quality = run_quality_gates(df)
    if not quality["passed"]:
        raise ValueError(
            "cleaned data failed quality gates: " + "; ".join(quality["issues"])
        )


def _validate_employer_locations(
    df: pd.DataFrame,
    locations: pd.DataFrame,
) -> None:
    if not _LOCATION_COLUMNS.issubset(locations.columns):
        raise ValueError(
            "employer_locations.csv is invalid; run geocode.py --employer-only"
        )

    expected = referenced_employers(df)
    actual = set(locations["employer_name"].dropna().astype(str).str.strip())
    if locations["employer_name"].isna().any() or expected != actual:
        raise ValueError(
            "employer_locations.csv does not match cleaned employers; "
            "run geocode.py --employer-only"
        )

    primary = locations["is_primary"].astype(str).str.lower().eq("true")
    primary_counts = primary.groupby(locations["employer_name"]).sum()
    if not primary_counts.eq(1).all():
        raise ValueError("each employer must have exactly one primary location row")

    valid_primary = (
        locations["is_primary"].astype(str).str.lower().isin({"true", "false"})
    )
    if not valid_primary.all():
        raise ValueError("employer_locations.csv contains an invalid is_primary value")

    trust = locations["address_trust"].fillna("").astype(str).str.strip()
    has_address = locations["employer_address"].fillna("").astype(str).str.strip().ne("")
    if (has_address & ~trust.isin(ADDRESS_TRUST_VALUES)).any():
        raise ValueError("employer_locations.csv contains an invalid address_trust value")


def _validate_contribution_fields(df: pd.DataFrame) -> None:
    donor_keys = df["donor_key"].fillna("").astype(str).str.strip()
    if donor_keys.eq("").any():
        raise ValueError("donor_key contains blank values")

    sub_ids = df["sub_id"].fillna("").astype(str).str.strip()
    if not sub_ids.str.fullmatch(r"\d+").all():
        raise ValueError("sub_id contains invalid values")

    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    if dates.isna().any():
        raise ValueError("contribution_receipt_date contains invalid values")

    cycles = pd.to_numeric(df["two_year_transaction_period"], errors="coerce")
    if cycles.isna().any():
        raise ValueError("two_year_transaction_period contains invalid values")


def _validate_committees(df: pd.DataFrame) -> None:
    committees = pd.read_csv(COMMITTEES_CSV, dtype=str, keep_default_na=False)
    known = set(committees["committee_short"].str.strip())
    received = set(df["recipient_committee"].dropna().astype(str).str.strip())
    unknown = sorted(received - known)
    if unknown:
        raise ValueError(
            "recipient_committee is missing from committees.csv: " + ", ".join(unknown)
        )


def _validate_input(df: pd.DataFrame, locations: pd.DataFrame) -> None:
    """Reject unfinished pipeline output."""
    _validate_cleaned_data(df)
    _validate_employer_locations(df, locations)
    _validate_contribution_fields(df)
    _validate_committees(df)


def _read_input() -> tuple[pd.DataFrame, list[dict]]:
    if not CLEANED_CSV.exists():
        raise FileNotFoundError(f"{CLEANED_CSV} not found; run the pipeline first")
    if not EMPLOYER_LOCATIONS_CSV.exists():
        raise FileNotFoundError(
            f"{EMPLOYER_LOCATIONS_CSV} not found; "
            "run geocode.py --employer-only"
        )

    df = read_pipeline_csv(CLEANED_CSV)
    locations = pd.read_csv(
        EMPLOYER_LOCATIONS_CSV,
        dtype=str,
        keep_default_na=False,
    )
    _validate_input(df, locations)

    amounts = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce")
    if amounts.isna().any():
        raise ValueError("contribution_receipt_amount contains invalid values")
    df["contribution_receipt_amount"] = amounts
    return df, load_employer_locations(locations)


def show_stats(cur: Any) -> None:
    logger.info("\n  -- Database Stats --")
    logger.info(f"  {'Name':35s} {'Type':8s} {'Rows':>10s}")
    logger.info(f"  {'-' * 35} {'-' * 8} {'-' * 10}")
    for names, kind in ((TABLES, "table"), (VIEWS, "view"), (MAT_VIEWS, "matview")):
        for name in names:
            logger.info(f"  {name:35s} {kind:8s} {_count(cur, name):>10,}")


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


def _check_reader(cur: Any) -> None:
    cur.execute(
        """
        SELECT
            rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls,
            EXISTS (
                SELECT 1 FROM pg_auth_members
                WHERE member = pg_roles.oid
            )
        FROM pg_roles
        WHERE rolname = %s
        """,
        (DATABASE_READER,),
    )
    role = cur.fetchone()

    if role is None:
        raise RuntimeError(f"Database role {DATABASE_READER} does not exist")
    if role[0]:
        raise RuntimeError(f"{DATABASE_READER} must be unprivileged")
    if role[1]:
        raise RuntimeError(f"{DATABASE_READER} must not inherit another role")


def grant_read_access(conn: Any, cur: Any) -> None:
    """Give fec_app table SELECT and remove sequence access."""
    logger.info("\n-- Granting permissions --")
    _check_reader(cur)

    reader = _quote_identifier(DATABASE_READER)

    statements = (
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM {reader}",
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM PUBLIC",
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM {reader}",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {reader}",
    )

    try:
        for statement in statements:
            cur.execute(statement)
        conn.commit()
    except Exception as error:
        conn.rollback()
        raise RuntimeError(f"Permission update failed: {error}") from error

    logger.info("  %s: table SELECT only; no sequence access", DATABASE_READER)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load FEC data into normalized PostgreSQL"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Drop all objects & reload"
    )
    args = parser.parse_args()

    if not args.reset:
        parser.error("use --reset to reload the database")

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

    logger.info("\n-- Analyzing tables --")
    for table in TABLES:
        if _count(cur, table) > 0:
            cur.execute(f"ANALYZE {table}")
    conn.commit()
    logger.info("  ANALYZE complete")

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
