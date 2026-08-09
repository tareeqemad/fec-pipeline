"""Load the lookup tables and the bulk reference tables from their CSVs."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import CLEANED_CSV, PROJECT_ROOT
from fec.log import get_logger

from ._base import _count

logger = get_logger(__name__)

REF_TABLES = [
    # CSV table mappings.
    ("us_states", "us_states.csv",
     ["state_fips", "code", "name"]),
    ("zcta_state_rel", "zcta_state_rel.csv",
     ["zcta5", "state_fips"]),
    ("zip_centroids", "zip_centroids.csv",
     ["zip", "lat", "lng", "source"]),
    # Editorial tables load later.
]

# Required reference columns.
NOT_NULL_COLS = {
    'us_states': ['state_fips', 'code', 'name'],
    'zcta_state_rel': ['zcta5', 'state_fips'],
    'zip_centroids': ['zip', 'lat', 'lng'],
}


def load_reference_tables(conn: Any, cur: Any) -> None:
    """Load reference tables from data/database/*.csv files."""
    db_dir = PROJECT_ROOT / "data" / "database"
    if not db_dir.exists():
        logger.info(f"\n  {db_dir} not found -- skipping reference tables")
        return

    logger.info("\n-- Loading reference tables --")

    for table_name, csv_file, columns in REF_TABLES:
        csv_path = db_dir / csv_file
        if not csv_path.exists():
            logger.info(f"  {csv_file} not found -- skipping {table_name}")
            continue

        start = time.time()
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

        # Keep shared CSV columns.
        use_cols = [column for column in columns if column in df.columns]
        if not use_cols:
            logger.warning(f"  No matching columns in {csv_file}")
            continue

        df = df[use_cols]

        # Empty cells become NULL.
        df = df.replace('', None)
        df = df.dropna(how='all')

        # Reject incomplete reference rows.
        for col in NOT_NULL_COLS.get(table_name, []):
            if col not in df.columns:
                raise ValueError(
                    f"{csv_file}: required column {col!r} missing "
                    f"(NOT NULL in {table_name})")
            missing = df[col].isna()
            if missing.any():
                # Include CSV headers.
                lines = [int(idx) + 2 for idx in df.index[missing][:10]]
                raise ValueError(
                    f"{csv_file}: {int(missing.sum())} row(s) missing required "
                    f"column {col!r} (NOT NULL in {table_name}) -- "
                    f"sample CSV line number(s): {lines}")

        # Propagate insert failures.
        cols_str = ", ".join(use_cols)
        rows = [tuple(None if pd.isna(value) else value for value in row)
                for row in df.itertuples(index=False, name=None)]

        if rows:
            execute_values(cur,
                f"INSERT INTO {table_name} ({cols_str}) VALUES %s ON CONFLICT DO NOTHING",
                rows, page_size=5000)
            conn.commit()

        logger.info(f"  {table_name}: {_count(cur, table_name):,} ({time.time()-start:.1f}s)")


def load_lookups(conn: Any, cur: Any) -> None:
    """Load occupation_categories from the cleaned CSV and committees from committees.csv (single source of truth)."""
    logger.info("-- Loading lookups --")

    df = pd.read_csv(CLEANED_CSV, usecols=['occupation_category'], dtype=str)
    categories = sorted(df['occupation_category'].dropna().unique())
    rows = [(category,) for category in categories]
    execute_values(cur, "INSERT INTO occupation_categories (name) VALUES %s ON CONFLICT DO NOTHING",
                   rows, page_size=50)
    conn.commit()
    logger.info("  occupation_categories: %d", _count(cur, 'occupation_categories'))

    from fec.committees import load_committees
    for committee in load_committees():
        committee_number = committee.get('committee_number')
        vals = (committee_number, committee['committee_name'], committee['committee_short'],
                committee['raised'], committee['spent'], committee['irs_990_link'],
                committee['logo_path'])
        if committee_number:
            cur.execute(
                "INSERT INTO committees (committee_number, committee_name, committee_short, raised, spent, irs_990_link, logo_path) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (committee_number) DO UPDATE SET "
                "committee_name=EXCLUDED.committee_name, committee_short=EXCLUDED.committee_short, "
                "raised=EXCLUDED.raised, spent=EXCLUDED.spent, irs_990_link=EXCLUDED.irs_990_link, "
                "logo_path=EXCLUDED.logo_path",
                vals)
        else:
            # Deduplicate non-FEC organizations.
            cur.execute(
                "INSERT INTO committees (committee_number, committee_name, committee_short, raised, spent, irs_990_link, logo_path) "
                "SELECT %s, %s, %s, %s, %s, %s, %s "
                "WHERE NOT EXISTS (SELECT 1 FROM committees WHERE committee_name = %s)",
                vals + (committee['committee_name'],))

    conn.commit()
    logger.info("  committees: %d", _count(cur, 'committees'))
