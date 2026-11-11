"""Connect to PostgreSQL, run the load as one transaction, and turn frames into rows."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pandas as pd
import psycopg2

from fec.common.log import get_logger
from fec.common.settings import get_db_config, load_env

logger = get_logger(__name__)


def connect() -> Any:
    """Connect to PostgreSQL with the settings from .env or the environment."""
    load_env()
    return psycopg2.connect(**get_db_config())


@contextmanager
def load_transaction(conn: Any) -> Iterator[None]:
    """Commit the whole load, or roll it back on any failure."""
    try:
        yield
    except BaseException:
        conn.rollback()
        logger.error("  load failed: rolled back, the database is unchanged")
        raise
    conn.commit()


def missing_as_none(frame: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with Python values, None where a value is missing."""
    return frame.astype(object).where(frame.notna(), None)


def to_rows(frame: pd.DataFrame) -> list[tuple]:
    """Return the frame's rows as tuples ready to insert, None where a value is missing."""
    return list(missing_as_none(frame).itertuples(index=False, name=None))


# Converters still used by the filing-table loaders.
import numpy as np  # noqa: E402


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
