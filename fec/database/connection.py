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
