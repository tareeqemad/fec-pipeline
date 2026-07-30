"""Shared pytest fixtures."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = PROJECT_ROOT / "fec" / "database" / "schema.sql"


def _connect(dsn, retries=20, delay=1.0):
    """Connect, waiting for the server to accept connections (CI service startup)."""
    import psycopg2
    last = None
    for _ in range(retries):
        try:
            return psycopg2.connect(dsn)
        except psycopg2.OperationalError as e:  # not ready yet
            last = e
            time.sleep(delay)
    raise last


@pytest.fixture(scope="session")
def pg():
    """DSN for a Postgres with schema.sql applied once; TEST_DATABASE_URL in CI, else testcontainers."""
    container = None
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.importorskip("testcontainers", reason="no TEST_DATABASE_URL and testcontainers not installed")
        from testcontainers.postgres import PostgresContainer
        container = PostgresContainer("postgres:18")
        container.start()
        dsn = (f"postgresql://test:test@{container.get_container_host_ip()}"
               f":{container.get_exposed_port(5432)}/test")

    conn = _connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;")
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        cur.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.close()

    yield dsn
    if container is not None:
        container.stop()


@pytest.fixture
def db(pg):
    """Per-test connection; everything is rolled back afterwards (fast isolation)."""
    import psycopg2
    conn = psycopg2.connect(pg)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()
