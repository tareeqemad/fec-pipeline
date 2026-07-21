"""database/loader/_base.py — connection, credentials, and low-level value/count helpers."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import load_env, get_db_config, get_db_roles, get_env
from fec.resolve.pipeline.constants import NOT_REAL_EMPLOYER

from fec.log import get_logger

logger = get_logger(__name__)


load_env()


PG = get_db_config()


ROLES = get_db_roles()


# Single source of truth: the SAME set the cleaning / resolve / build_employers
# use, so a job title or industry word (PHYSICIAN, FINANCE, …) added there never
# leaks into the employers table here. NOT_REAL_EMPLOYER already includes
# SKIP_EMPLOYERS | REFUSAL_EMPLOYERS and the SELF/UNEMPLOYED variants.
NON_EMPLOYER_STATUSES = NOT_REAL_EMPLOYER


def connect(dbname: Optional[str] = None) -> Any:
    """Connect to PostgreSQL using credentials from .env."""
    return psycopg2.connect(
        host=PG["host"],
        port=PG["port"],
        dbname=dbname or PG["dbname"],
        user=PG["user"],
        password=PG["password"],
    )


def init_roles() -> None:
    """Create PostgreSQL roles and database. Requires POSTGRES_PASSWORD in .env."""
    pg_pass = get_env("POSTGRES_PASSWORD", required=True)

    conn = psycopg2.connect(
        host=PG["host"],
        port=PG["port"],
        dbname="postgres", user="postgres", password=pg_pass,
    )
    conn.autocommit = True
    cur = conn.cursor()

    for role, pw in ROLES.items():
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cur.fetchone():
            logger.info("  ✓ %s (exists)", role)
        else:
            cur.execute(f"CREATE ROLE {role} WITH LOGIN PASSWORD %s", (pw,))
            logger.info("  ✓ %s (created)", role)

    db = PG["dbname"]
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,))
    if not cur.fetchone():
        cur.execute(f"CREATE DATABASE {db} OWNER fec_owner")
        logger.info("  ✓ %s (created)", db)
    else:
        logger.info("  ✓ %s (exists)", db)

    conn.close()


def to_native(val: Any) -> Any:
    """Convert pandas value to Python-native (None for NaN)."""
    if pd.isna(val): return None
    if isinstance(val, np.integer):  return int(val)
    if isinstance(val, np.floating): return float(val)
    if isinstance(val, np.bool_):    return bool(val)
    return val


def to_float_or_none(val: Any) -> Optional[float]:
    """Coerce to native Python float; None for None/NaN/empty string.

    Used for nullable DOUBLE PRECISION columns (latitude, longitude,
    amount, …) when the source DataFrame is loaded with dtype=str (to
    preserve literal 'NULL' surnames). Without this, lat/lng arrive as
    strings and `execute_values(..., UPDATE FROM VALUES)` fails with
    "double precision but expression is of type text".

    Raises ValueError on non-empty unparseable garbage — silently
    storing NULL would drop a real value on the floor.
    """
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"to_float_or_none: cannot parse {s!r} as float")


def to_int_or_none(val: Any) -> Optional[int]:
    """Coerce to native Python int; None for None/NaN/empty string.

    psycopg2 can't handle numpy.int64 on Python 3.14, so FK/cycle columns
    go through here before the INSERT. Raises ValueError on non-empty
    unparseable garbage — silently storing NULL would drop a real value.
    """
    if val is None or pd.isna(val):
        return None
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        val = s
    try:
        return int(val)
    except (ValueError, TypeError):
        raise ValueError(f"to_int_or_none: cannot parse {val!r} as int")


def _count(cur: Any, table: str) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
    except Exception as e:
        cur.connection.rollback()
        logger.warning("_count(%s) failed — returning 0: %s", table, e)
        return 0
