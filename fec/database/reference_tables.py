"""Load occupation categories, committees and the state and ZIP reference tables."""

from __future__ import annotations

from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.common.committees import read_committees
from fec.common.settings import REFERENCE_DIR
from fec.database.connection import to_rows

COMMITTEE_COLUMNS = [
    "committee_number",
    "committee_name",
    "committee_short",
    "raised",
    "spent",
    "irs_990_link",
    "logo_path",
]

# Each table is loaded from <table>.csv with these columns.
REFERENCE_TABLES = {
    "us_states": ["state_fips", "code", "name"],
    "zcta_state_rel": ["zcta5", "state_fips"],
    "zip_centroids": ["zip", "lat", "lng", "source"],
}


def load_occupation_categories(cur: Any, df: pd.DataFrame) -> None:
    """Load the occupation categories of the filings and add occupation_category_id to df."""
    names = sorted(df["occupation_category"].dropna().unique())
    sql = "INSERT INTO occupation_categories (name) VALUES %s RETURNING name, occupation_category_id"
    category_ids = dict(execute_values(cur, sql, [(name,) for name in names], fetch=True))
    df["occupation_category_id"] = df["occupation_category"].map(category_ids)


def load_committees(cur: Any) -> None:
    """Load committees.csv."""
    rows = [tuple(committee[column] for column in COMMITTEE_COLUMNS) for committee in read_committees()]
    execute_values(cur, f"INSERT INTO committees ({', '.join(COMMITTEE_COLUMNS)}) VALUES %s", rows)


def committee_ids_by_short_name(cur: Any) -> dict[str, int]:
    """Return the committee IDs by short name."""
    cur.execute("SELECT committee_short, committee_id FROM committees WHERE committee_short IS NOT NULL")
    return dict(cur.fetchall())


def load_reference_tables(cur: Any) -> None:
    """Load the state and ZIP reference CSV files."""
    for table, columns in REFERENCE_TABLES.items():
        frame = pd.read_csv(REFERENCE_DIR / f"{table}.csv", dtype=str, keep_default_na=False, na_values=[""])
        execute_values(
            cur, f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s", to_rows(frame[columns]), page_size=5000
        )
