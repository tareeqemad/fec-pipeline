"""Database connection and value conversion."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import (
    get_db_config,
    load_env,
)
from fec.log import get_logger

logger = get_logger(__name__)


# Read database settings when connecting.
def db_config() -> dict:
    load_env()
    return get_db_config()


# Quote an SQL identifier and escape embedded quotes.
def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# Connect to PostgreSQL using the configured credentials.
def connect(dbname: str | None = None) -> Any:
    config = db_config()
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=dbname or config["dbname"],
        user=config["user"],
        password=config["password"],
    )


# Pandas values to Python values, None when missing.
def to_native(val: Any) -> Any:
    if pd.isna(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


# Read a float or None; reject invalid text.
def to_float_or_none(val: Any) -> float | None:
    if val is None or pd.isna(val):
        return None
    stripped = str(val).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        raise ValueError(f"to_float_or_none: cannot parse {stripped!r} as float")


# Read an integer or None; reject invalid text.
def to_int_or_none(val: Any) -> int | None:
    if val is None or pd.isna(val):
        return None
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        val = stripped
    try:
        return int(val)
    except (ValueError, TypeError):
        raise ValueError(f"to_int_or_none: cannot parse {val!r} as int")


# Commit the whole load, or roll it back on failure.
@contextmanager
def load_transaction(conn: Any) -> Iterator[None]:
    try:
        yield
    except BaseException:
        conn.rollback()
        logger.error("  load failed: rolled back, the database is unchanged")
        raise
    conn.commit()


# Log the failed stage before the transaction rolls back.
@contextmanager
def stage(name: str) -> Iterator[None]:
    try:
        yield
    except BaseException:
        logger.error("  stage '%s' failed", name)
        raise


# Roll back this block if it fails.
@contextmanager
def savepoint(cur: Any, name: str) -> Iterator[None]:
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    cur.execute(f"RELEASE SAVEPOINT {name}")


# Read the row count, or warn and return zero.
def _count(cur: Any, table: str) -> int:
    try:
        with savepoint(cur, "count_rows"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    except Exception as error:
        logger.warning("_count(%s) failed -- returning 0: %s", table, error)
        return 0
