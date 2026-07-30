"""Connection, credentials, and low-level value/count helpers."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

try:
    import psycopg2
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.config.constants import NOT_REAL_EMPLOYER
from fec.env import get_db_config, get_db_roles, get_env, load_env
from fec.log import get_logger

logger = get_logger(__name__)

load_env()

PG = get_db_config()
ROLES = get_db_roles()

# The SAME set cleaning / resolve / build_employers use, so a job title or
# industry word added there never leaks into the employers table here.
NON_EMPLOYER_STATUSES = NOT_REAL_EMPLOYER


def connect(dbname: str | None = None) -> Any:
    """Connect to PostgreSQL using credentials from .env."""
    return psycopg2.connect(
        host=PG["host"],
        port=PG["port"],
        dbname=dbname or PG["dbname"],
        user=PG["user"],
        password=PG["password"],
    )


def init_roles() -> None:
    """First-run setup as the postgres superuser: roles, database, extensions and grants; idempotent; requires POSTGRES_PASSWORD in .env."""
    pg_pass = get_env("POSTGRES_PASSWORD", required=True)

    conn = psycopg2.connect(
        host=PG["host"],
        port=PG["port"],
        dbname="postgres", user="postgres", password=pg_pass,
    )
    conn.autocommit = True
    cur = conn.cursor()

    for role, role_password in ROLES.items():
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,))
        if cur.fetchone():
            logger.info("  %s (exists)", role)
        else:
            cur.execute(f"CREATE ROLE {role} WITH LOGIN PASSWORD %s", (role_password,))
            logger.info("  %s (created)", role)

    db = PG["dbname"]
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,))
    if not cur.fetchone():
        cur.execute(f"CREATE DATABASE {db} OWNER fec_owner")
        logger.info("  %s (created)", db)
    else:
        logger.info("  %s (exists)", db)

    conn.close()

    # extensions + schema grants need a superuser session INSIDE the database
    owner = PG["user"]
    conn = psycopg2.connect(
        host=PG["host"], port=PG["port"],
        dbname=db, user="postgres", password=pg_pass,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"GRANT ALL ON SCHEMA public TO {owner}")
    for extension in ('pg_trgm', 'cube', 'earthdistance'):
        try:
            cur.execute(f"CREATE EXTENSION IF NOT EXISTS {extension}")
        except Exception as error:
            logger.warning(f"  Could not create extension {extension}: {error}")
    cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {owner}")
    cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {owner}")
    conn.close()
    logger.info("  Granted ALL on schema public to %s; extensions ready (pg_trgm, cube, earthdistance)", owner)


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


def _count(cur: Any, table: str) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]
    except Exception as error:
        cur.connection.rollback()
        logger.warning("_count(%s) failed -- returning 0: %s", table, error)
        return 0
