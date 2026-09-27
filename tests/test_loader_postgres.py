"""The loader against a real PostgreSQL: constraints, views, and a failed reload.

Runs only when FEC_TEST_PG_DBNAME names a throwaway database set up like
fec_db in the README (fec_owner owns it, fec_app exists, pg_trgm, cube and
earthdistance installed); otherwise it is skipped. It drops and reloads that
database, so never point it at the real one.
"""
import os
import sys

import pandas as pd
import pytest

psycopg2 = pytest.importorskip("psycopg2")

from fec.database.loader import run, validate  # noqa: E402
from fec.pipeline_run import record, start_run  # noqa: E402

TEST_DB = os.getenv("FEC_TEST_PG_DBNAME", "")
pytestmark = pytest.mark.skipif(
    not TEST_DB or TEST_DB == "fec_db",
    reason="set FEC_TEST_PG_DBNAME (and FEC_TEST_PG_HOST/PORT/USER/PASSWORD) to a throwaway database",
)

ROW = {
    "sub_id": "1", "transaction_id": "T1", "two_year_transaction_period": "2024",
    "recipient_committee": "AIPAC", "entity_type": "INDIVIDUAL",
    "contributor_name": "DOE, JANE", "contributor_first_name": "JANE", "contributor_last_name": "DOE",
    "contributor_street_1": "1 MAIN ST", "contributor_street_2": "", "contributor_city": "NEW YORK",
    "contributor_state": "NY", "contributor_zip": "10001", "contributor_employer": "ACME INC",
    "contributor_occupation": "CEO", "occupation_category": "EXECUTIVE / C-SUITE",
    "contribution_receipt_date": "2024-01-01", "contribution_receipt_amount": "500",
    "previous_employer": "", "identity_status": "confirmed", "employment_source": "filed",
    "donor_key": "donor-1", "latitude": "40.7", "longitude": "-74.0",
    "employer_status": "active",
}
LOCATION = {
    "employer_name": "ACME INC", "employer_address": "2 WORK ST", "employer_city": "NEW YORK",
    "employer_state": "NY", "employer_zip": "10001", "employer_latitude": "40.7",
    "employer_longitude": "-74.0", "is_primary": "true", "address_source": "manual",
    "address_trust": "verified",
}


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    for name in ("HOST", "PORT", "USER", "PASSWORD"):
        value = os.getenv(f"FEC_TEST_PG_{name}")
        if value:
            monkeypatch.setenv(f"PG_{name}", value)
    monkeypatch.setenv("PG_DBNAME", TEST_DB)
    cleaned, locations = tmp_path / "contributions_cleaned.csv", tmp_path / "employer_locations.csv"
    pd.DataFrame([ROW]).to_csv(cleaned, index=False)
    pd.DataFrame([LOCATION]).to_csv(locations, index=False)
    start_run(cleaned)
    record("employers", cleaned, locations)
    monkeypatch.setattr(validate, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(validate, "EMPLOYER_LOCATIONS_CSV", locations)
    monkeypatch.setattr(sys, "argv", ["loader.py", "--reset"])
    # the rosters name real donors this one-row file does not have; they have their own tests
    monkeypatch.setattr(run, "load_leadership", lambda conn, cur: None)
    monkeypatch.setattr(run, "load_key_accomplices", lambda conn, cur: None)
    return run.connect


def _count(connect, table):
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_a_load_builds_tables_views_and_constraints(test_database):
    run.main()
    assert _count(test_database, "contributions") == 1
    assert _count(test_database, "mv_donor_profile") >= 1
    conn = test_database()
    try:
        with conn.cursor() as cur, pytest.raises(psycopg2.IntegrityError):
            cur.execute("INSERT INTO contributions SELECT * FROM contributions")  # sub_id is unique
    finally:
        conn.rollback()
        conn.close()


def test_a_failed_reload_leaves_the_previous_load(test_database, monkeypatch):
    run.main()
    before = _count(test_database, "contributions")

    def fail(*args, **kwargs):
        raise RuntimeError("load failed after the old tables were dropped")

    monkeypatch.setattr(run, "load_all", fail)
    with pytest.raises(RuntimeError, match="old tables were dropped"):
        run.main()
    assert _count(test_database, "contributions") == before == 1
    assert _count(test_database, "mv_donor_profile") >= 1
