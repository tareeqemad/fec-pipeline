"""Loader ownership and permission checks."""
import pandas as pd
import pytest

import fec.database.loader as loader
from fec.database.loader import _base, grant_read_access
from fec.database.loader._base import _first_run_access_statements
from fec.database.loader.schema_reset import reset_schema
from fec.env import DATABASE_OWNER, DATABASE_READER


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ResetCursor:
    def __init__(
        self,
        user=DATABASE_OWNER,
        matviews=(),
        views=(),
        tables=(),
        leftovers=(),
        fail_drop=None,
    ):
        self.user = user
        self.objects = {
            "pg_matviews": list(matviews),
            "pg_views": list(views),
            "pg_tables": list(tables),
        }
        self.leftovers = list(leftovers)
        self.fail_drop = fail_drop
        self.drop_count = 0
        self.queries = []
        self.object_reads = 0
        self.row = None
        self.rows = []

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        self.queries.append(sql)

        if sql == "SELECT current_user":
            self.row = (self.user,)
        elif "FROM pg_matviews" in sql and "UNION ALL" in sql:
            self.object_reads += 1
            if self.object_reads == 1:
                self.rows = (
                    [(name, "MATERIALIZED VIEW", owner) for name, owner in self.objects["pg_matviews"]]
                    + [(name, "VIEW", owner) for name, owner in self.objects["pg_views"]]
                    + [(name, "TABLE", owner) for name, owner in self.objects["pg_tables"]]
                )
            else:
                self.rows = self.leftovers
        elif sql.startswith("DROP "):
            self.drop_count += 1
            if self.drop_count == self.fail_drop:
                raise RuntimeError("drop failed")

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class PermissionCursor:
    def __init__(self, role=(False, False)):
        self.role = role
        self.queries = []
        self.row = None

    def execute(self, query, params=None):
        sql = " ".join(query.split())
        self.queries.append(sql)
        if "FROM pg_roles" in sql:
            self.row = self.role

    def fetchone(self):
        return self.row


def test_reset_rejects_wrong_user_before_drop():
    conn = Connection()
    cur = ResetCursor(user=DATABASE_READER)

    with pytest.raises(RuntimeError, match="Loader must run as"):
        reset_schema(conn, cur)

    assert not any(query.startswith("DROP ") for query in cur.queries)
    assert conn.commits == 0


def test_reset_rejects_wrong_owner_before_drop():
    conn = Connection()
    cur = ResetCursor(tables=(("donors", DATABASE_READER),))

    with pytest.raises(RuntimeError, match="Ownership check failed"):
        reset_schema(conn, cur)

    assert not any(query.startswith("DROP ") for query in cur.queries)
    assert conn.commits == 0


def test_reset_rolls_back_every_drop():
    conn = Connection()
    tables = (("donors", DATABASE_OWNER), ("addresses", DATABASE_OWNER))
    cur = ResetCursor(tables=tables, fail_drop=2)

    with pytest.raises(RuntimeError, match="Schema reset failed"):
        reset_schema(conn, cur)

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_reset_drops_owned_objects():
    conn = Connection()
    cur = ResetCursor(
        matviews=(("mv_donor_profile", DATABASE_OWNER),),
        views=(("v_donor_profile", DATABASE_OWNER),),
        tables=(("donors", DATABASE_OWNER),),
    )

    reset_schema(conn, cur)

    assert cur.drop_count == 3
    assert conn.commits == 0
    assert conn.rollbacks == 0


def test_reader_gets_select_only():
    conn = Connection()
    cur = PermissionCursor()

    grant_read_access(conn, cur)

    sql = "\n".join(cur.queries)
    assert f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{DATABASE_READER}"' in sql
    assert f'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM "{DATABASE_READER}"' in sql
    assert f'REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM "{DATABASE_READER}"' in sql
    assert "GRANT SELECT ON ALL SEQUENCES" not in sql
    assert "ON DATABASE" not in sql
    assert "ALTER DEFAULT PRIVILEGES" not in sql
    assert "GRANT USAGE ON SCHEMA" not in sql
    assert "GRANT ALL" not in sql
    assert conn.commits == 1
    assert conn.rollbacks == 0


def test_reader_cannot_inherit_roles():
    conn = Connection()
    cur = PermissionCursor(role=(False, True))

    with pytest.raises(RuntimeError, match="must not inherit"):
        grant_read_access(conn, cur)

    assert conn.commits == 0


def test_first_run_owns_database_access_policy():
    sql = "\n".join(_first_run_access_statements("fec_db"))

    assert 'REVOKE ALL PRIVILEGES ON DATABASE "fec_db" FROM PUBLIC' in sql
    assert f'GRANT CONNECT ON DATABASE "fec_db" TO "{DATABASE_READER}"' in sql
    assert "REVOKE ALL PRIVILEGES ON SCHEMA public FROM PUBLIC" in sql
    assert f'GRANT USAGE ON SCHEMA public TO "{DATABASE_READER}"' in sql
    assert "ALTER DEFAULT PRIVILEGES" in sql


def test_postgres_connection_falls_back_to_local_socket(monkeypatch):
    calls = []

    def fake_connect(**options):
        calls.append(options)
        if "host" in options:
            raise _base.psycopg2.OperationalError("TCP blocked")
        return "socket connection"

    monkeypatch.setattr(_base, "PG", {"host": "localhost", "port": "5432"})
    monkeypatch.setattr(_base.psycopg2, "connect", fake_connect)

    result = _base._connect_as_postgres("postgres", "secret")

    assert result == "socket connection"
    assert calls[0]["host"] == "localhost"
    assert "host" not in calls[1]


def test_postgres_connection_does_not_fallback_for_remote_host(monkeypatch):
    calls = []

    def fake_connect(**options):
        calls.append(options)
        raise _base.psycopg2.OperationalError("TCP blocked")

    monkeypatch.setattr(_base, "PG", {"host": "db.example.com", "port": "5432"})
    monkeypatch.setattr(_base.psycopg2, "connect", fake_connect)

    with pytest.raises(_base.psycopg2.OperationalError, match="TCP blocked"):
        _base._connect_as_postgres("postgres", "secret")

    assert len(calls) == 1


def test_loader_requires_exact_ready_employer_names(tmp_path, monkeypatch):
    committee_file = tmp_path / "committees.csv"
    committee_file.write_text("committee_short\nAIPAC\n", encoding="utf-8")
    monkeypatch.setattr(loader, "COMMITTEES_CSV", committee_file)

    df = pd.DataFrame([{
        "sub_id": "1", "transaction_id": "T1",
        "two_year_transaction_period": "2024",
        "recipient_committee": "AIPAC", "entity_type": "INDIVIDUAL",
        "contributor_name": "DOE, JANE", "contributor_first_name": "JANE",
        "contributor_last_name": "DOE", "contributor_street_1": "1 MAIN ST",
        "contributor_street_2": "", "contributor_city": "NEW YORK",
        "contributor_state": "NY", "contributor_zip": "10001",
        "contributor_employer": "ACME INC", "contributor_occupation": "CEO",
        "occupation_category": "EXECUTIVE / C-SUITE",
        "contribution_receipt_date": "2024-01-01",
        "contribution_receipt_amount": "500", "previous_employer": "",
        "donor_key": "donor-1", "latitude": "40.7", "longitude": "-74.0",
        "employer_status": "active",
    }])
    locations = pd.DataFrame([{
        "employer_name": "ACME INC", "employer_address": "2 WORK ST",
        "employer_city": "NEW YORK", "employer_state": "NY",
        "employer_zip": "10001", "employer_latitude": "40.7",
        "employer_longitude": "-74.0", "is_primary": "true",
        "address_source": "manual", "address_trust": "verified",
    }])

    loader._validate_input(df, locations)

    locations.loc[0, "employer_name"] = "ACME, INC."
    with pytest.raises(ValueError, match="does not match cleaned employers"):
        loader._validate_input(df, locations)
