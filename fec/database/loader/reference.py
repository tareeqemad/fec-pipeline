"""Load occupation categories, committees and reference tables."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.committees import load_committees
from fec.env import CLEANED_CSV, REFERENCE_DIR
from fec.log import get_logger

logger = get_logger(__name__)

# Each table reads <table>.csv: required columns first, then optional columns.
REF_TABLES = [
    ("us_states", ["state_fips", "code", "name"], []),
    ("zcta_state_rel", ["zcta5", "state_fips"], []),
    ("zip_centroids", ["zip", "lat", "lng"], ["source"]),
]


# Read reference values and reject missing required columns or cells.
def _read_reference_rows(path: Path, table: str, required: list[str], optional: list[str]):
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = [column for column in required + optional if column in frame.columns]
    if not columns:
        logger.warning(f"  No matching columns in {path.name}")
        return [], []

    frame = frame[columns].replace('', None).dropna(how='all')
    for column in required:
        if column not in frame.columns:
            raise ValueError(
                f"{path.name}: required column {column!r} missing "
                f"(NOT NULL in {table})")
        missing = frame[column].isna()
        if missing.any():
            lines = [int(index) + 2 for index in frame.index[missing][:10]]
            raise ValueError(
                f"{path.name}: {int(missing.sum())} row(s) missing required "
                f"column {column!r} (NOT NULL in {table}) -- "
                f"sample CSV line number(s): {lines}")

    rows = [tuple(None if pd.isna(value) else value for value in row)
            for row in frame.itertuples(index=False, name=None)]
    return columns, rows


# Load the available reference CSV files.
def load_reference_tables(cur: Any) -> None:
    if not REFERENCE_DIR.exists():
        logger.info(f"\n  {REFERENCE_DIR} not found -- skipping reference tables")
        return

    for table_name, required, optional in REF_TABLES:
        csv_path = REFERENCE_DIR / f"{table_name}.csv"
        if not csv_path.exists():
            logger.info(f"  {csv_path.name} not found -- skipping {table_name}")
            continue

        columns, rows = _read_reference_rows(csv_path, table_name, required, optional)
        if not columns:
            continue

        if rows:
            columns_sql = ", ".join(columns)
            execute_values(cur,
                f"INSERT INTO {table_name} ({columns_sql}) VALUES %s ON CONFLICT DO NOTHING",
                rows, page_size=5000)


# Load occupation categories and committees.
def load_lookups(cur: Any) -> None:
    _load_occupation_categories(cur)
    _load_committees(cur)


# Load the occupation categories present in the cleaned file.
def _load_occupation_categories(cur: Any) -> None:
    df = pd.read_csv(CLEANED_CSV, usecols=['occupation_category'], dtype=str)
    categories = sorted(df['occupation_category'].dropna().unique())
    rows = [(category,) for category in categories]
    execute_values(cur, "INSERT INTO occupation_categories (name) VALUES %s ON CONFLICT DO NOTHING",
                   rows, page_size=50)


# Load committees without duplicating non-FEC organizations.
def _load_committees(cur: Any) -> None:
    insert_sql = (
        "INSERT INTO committees (committee_number, committee_name, committee_short, "
        "raised, spent, irs_990_link, logo_path) "
    )
    for committee in load_committees():
        committee_number = committee.get('committee_number')
        values = (
            committee_number, committee['committee_name'], committee['committee_short'],
            committee['raised'], committee['spent'], committee['irs_990_link'],
            committee['logo_path'],
        )
        if committee_number:
            cur.execute(
                insert_sql + "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (committee_number) DO UPDATE SET "
                "committee_name=EXCLUDED.committee_name, "
                "committee_short=EXCLUDED.committee_short, "
                "raised=EXCLUDED.raised, spent=EXCLUDED.spent, "
                "irs_990_link=EXCLUDED.irs_990_link, "
                "logo_path=EXCLUDED.logo_path",
                values)
        else:
            # Deduplicate non-FEC organizations.
            cur.execute(
                insert_sql + "SELECT %s, %s, %s, %s, %s, %s, %s "
                "WHERE NOT EXISTS (SELECT 1 FROM committees WHERE committee_name = %s)",
                values + (committee['committee_name'],))
