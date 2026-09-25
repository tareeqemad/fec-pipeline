"""Connection, credentials, and low-level value/count helpers."""
from __future__ import annotations

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

load_env()

PG = get_db_config()

# double-quote and escape a sql identifier
def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


# open a postgresql connection using .env credentials
def connect(dbname: str | None = None) -> Any:
    """Connect to PostgreSQL using credentials from .env."""
    return psycopg2.connect(
        host=PG["host"],
        port=PG["port"],
        dbname=dbname or PG["dbname"],
        user=PG["user"],
        password=PG["password"],
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


# row count for a table, 0 if the query fails
def _count(cur: Any, table: str) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
    except Exception as error:
        cur.connection.rollback()
        logger.warning("_count(%s) failed -- returning 0: %s", table, error)
        return 0
