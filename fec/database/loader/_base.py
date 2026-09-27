"""Connection, credentials, and low-level value/count helpers."""
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


# the connection settings, read from .env when a connection is made (not on import)
def db_config() -> dict:
    load_env()
    return get_db_config()


# double-quote and escape a sql identifier
def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# open a postgresql connection using .env credentials
def connect(dbname: str | None = None) -> Any:
    """Connect to PostgreSQL using credentials from .env."""
    config = db_config()
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=dbname or config["dbname"],
        user=config["user"],
        password=config["password"],
    )




# convert a pandas/numpy value to plain python, nan to none
def to_native(val: Any) -> Any:
    """Convert pandas value to Python-native (None for NaN)."""
    if pd.isna(val):
        return None
    if isinstance(val, np.integer):
        return int(val)
    if isinstance(val, np.floating):
        return float(val)
    if isinstance(val, np.bool_):
        return bool(val)
    return val


# parse a value to float or none, raise on garbage
def to_float_or_none(val: Any) -> float | None:
    """Native float, or None for None/NaN/empty (dtype=str frames); raises ValueError on unparseable garbage rather than dropping a value."""
    if val is None or pd.isna(val):
        return None
    stripped = str(val).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        raise ValueError(f"to_float_or_none: cannot parse {stripped!r} as float")


# parse a value to int or none, raise on garbage
def to_int_or_none(val: Any) -> int | None:
    """Native int (psycopg2 can't take numpy.int64), or None for None/NaN/empty; raises ValueError on unparseable garbage."""
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


# the whole reload in one transaction: all of it is saved, or none of it
@contextmanager
def load_transaction(conn: Any) -> Iterator[None]:
    """The only place the loader commits. A failure at any stage, even after
    the old tables were dropped, rolls everything back: the database keeps
    the previous load exactly as it was."""
    try:
        yield
    except BaseException:
        conn.rollback()
        logger.error("  load failed: rolled back, the database is unchanged")
        raise
    conn.commit()


# name the stage a failure happened in; the transaction decides the rest
@contextmanager
def stage(conn: Any, name: str) -> Iterator[None]:
    try:
        yield
    except BaseException:
        logger.error("  stage '%s' failed", name)
        raise


# undo only the statements inside the block when it fails
@contextmanager
def savepoint(cur: Any, name: str) -> Iterator[None]:
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    cur.execute(f"RELEASE SAVEPOINT {name}")


# row count for a table, 0 if the query fails
def _count(cur: Any, table: str) -> int:
    try:
        with savepoint(cur, "count_rows"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    except Exception as error:
        logger.warning("_count(%s) failed -- returning 0: %s", table, error)
        return 0
