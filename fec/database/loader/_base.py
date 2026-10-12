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


# read .env settings on connect, not import
def db_config() -> dict:
    load_env()
    return get_db_config()


# double-quote and escape a sql identifier
def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# open a postgresql connection using .env credentials
def connect(dbname: str | None = None) -> Any:
    config = db_config()
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=dbname or config["dbname"],
        user=config["user"],
        password=config["password"],
    )


# numpy value to Python; NaN to None
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


# float or None; raise on unparseable text
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


# native int or None; raise on garbage
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


# only commit point: all saved or none
@contextmanager
def load_transaction(conn: Any) -> Iterator[None]:
    try:
        yield
    except BaseException:
        conn.rollback()
        logger.error("  load failed: rolled back, the database is unchanged")
        raise
    conn.commit()


# log which stage failed, then re-raise
@contextmanager
def stage(conn: Any, name: str) -> Iterator[None]:
    try:
        yield
    except BaseException:
        logger.error("  stage '%s' failed", name)
        raise


# roll back only this block on failure
@contextmanager
def savepoint(cur: Any, name: str) -> Iterator[None]:
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    cur.execute(f"RELEASE SAVEPOINT {name}")


# row count of one table
def count_rows(cur: Any, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]
