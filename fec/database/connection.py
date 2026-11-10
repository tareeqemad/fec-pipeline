"""Database connection and value conversion."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
import pandas as pd

from fec.common.log import get_logger
from fec.common.settings import get_db_config, load_env

try:
    import psycopg2
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

logger = get_logger(__name__)


def db_config() -> dict:
    """Read database settings when connecting."""
    load_env()
    return get_db_config()


def quote_identifier(value: str) -> str:
    """Quote an SQL identifier and escape embedded quotes."""
    return '"' + value.replace('"', '""') + '"'


def connect(dbname: str | None = None) -> Any:
    """Connect to PostgreSQL using the configured credentials."""
    config = db_config()
    return psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=dbname or config["dbname"],
        user=config["user"],
        password=config["password"],
    )


def to_native(value: Any) -> Any:
    """Pandas values to Python values, None when missing."""
    if pd.isna(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def to_float_or_none(value: Any) -> float | None:
    """Read a float or None; reject invalid text."""
    if value is None or pd.isna(value):
        return None
    stripped = str(value).strip()
    if not stripped:
        return None
    try:
        return float(stripped)
    except ValueError:
        raise ValueError(f"to_float_or_none: cannot parse {stripped!r} as float")


def to_int_or_none(value: Any) -> int | None:
    """Read an integer or None; reject invalid text."""
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        value = stripped
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"to_int_or_none: cannot parse {value!r} as int")


@contextmanager
def load_transaction(conn: Any) -> Iterator[None]:
    """Commit the whole load, or roll it back on failure."""
    try:
        yield
    except BaseException:
        conn.rollback()
        logger.error("  load failed: rolled back, the database is unchanged")
        raise
    conn.commit()


@contextmanager
def logged_stage(name: str) -> Iterator[None]:
    """Log the failed stage before the transaction rolls back."""
    try:
        yield
    except BaseException:
        logger.error("  stage '%s' failed", name)
        raise
