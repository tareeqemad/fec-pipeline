"""Shared address loading and links."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import EMPLOYER_LOCATIONS_CSV
from fec.log import get_logger

from ._base import _count, to_float_or_none, to_native

logger = get_logger(__name__)


def load_employer_locations(frame: pd.DataFrame | None = None) -> list[dict]:
    """Read resolved employer locations."""
    if frame is None:
        if not EMPLOYER_LOCATIONS_CSV.exists():
            return []
        frame = pd.read_csv(
            EMPLOYER_LOCATIONS_CSV,
            dtype=str,
            keep_default_na=False,
        )
    locations = []
    for row in frame.to_dict("records"):
        if not row.get("employer_address"):
            continue
        locations.append({
            "employer_name": row["employer_name"],
            "employer_address": row.get("employer_address"),
            "employer_city": row.get("employer_city"),
            "employer_state": row.get("employer_state"),
            "employer_zip": row.get("employer_zip"),
            "employer_latitude": row.get("employer_latitude"),
            "employer_longitude": row.get("employer_longitude"),
            "is_primary": str(row.get("is_primary")).lower() == "true",
        })
    return locations


def _akey(st1, st2, city, state, z):
    """Normalized address tuple, '' for empty parts, matching the COALESCE(col,'') shape read back from addresses."""
    return (to_native(st1) or '', to_native(st2) or '', to_native(city) or '',
            to_native(state) or '', to_native(z) or '')


def load_address_dimension(
    conn: Any,
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict] | None = None,
) -> dict:
    """Step 4: load donor and employer addresses."""
    logger.info("\n-- 4/8 Loading addresses (shared dimension) --")
    start = time.time()

    addr_dim = {}

    def _add_addr(st1, st2, city, state, z, lat, lng):
        key = _akey(st1, st2, city, state, z)
        entry = addr_dim.get(key)
        if entry is None:
            addr_dim[key] = [to_native(st1), to_native(st2), to_native(city),
                             to_native(state), to_native(z), lat, lng]
        elif entry[5] is None and lat is not None:  # Backfill coordinates.
            entry[5], entry[6] = lat, lng

    # Donor addresses.
    for (street_1, street_2, city, state, zip_code), group in df.groupby(
        ['contributor_street_1', 'contributor_street_2', 'contributor_city',
         'contributor_state', 'contributor_zip'], dropna=False
    ):
        first_row = group.iloc[0]
        _add_addr(street_1, street_2, city, state, zip_code,
                  to_float_or_none(first_row.get('latitude')),
                  to_float_or_none(first_row.get('longitude')))

    locations = employer_locations
    if locations is None:
        locations = load_employer_locations()
    for location in locations:
        _add_addr(
            location["employer_address"], None,
            location["employer_city"], location["employer_state"],
            location["employer_zip"],
            to_float_or_none(location.get("employer_latitude")),
            to_float_or_none(location.get("employer_longitude")),
        )

    addr_keys = list(addr_dim.keys())
    execute_values(cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(addr_dim[key]) for key in addr_keys], page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)")
    conn.commit()

    # Match nullable fields.
    cur.execute("""SELECT address_id, COALESCE(street_1,''), COALESCE(street_2,''),
                          COALESCE(city,''), COALESCE(state_code,''), COALESCE(zip_code,'')
                   FROM addresses""")
    addr_dim_id = {}
    for row in cur.fetchall():
        addr_dim_id[(row[1], row[2], row[3], row[4], row[5])] = row[0]
    logger.info(f"  addresses: {_count(cur, 'addresses'):,} ({time.time()-start:.1f}s)")
    return addr_dim_id


def load_donor_addresses(conn: Any, cur: Any, df: pd.DataFrame,
                         donor_key_to_id: dict, addr_dim_id: dict) -> dict:
    """Step 5: donor_addresses link rows; returns (donor_id, st1, st2, city, state, zip) -> donor_address_id."""
    logger.info("\n-- 5/8 Loading donor addresses --")
    start = time.time()

    addr_key_to_id = {}
    donor_address_rows = []
    for (donor_key, street_1, street_2, city, state, zip_code), _group in df.groupby(
        ['donor_key', 'contributor_street_1', 'contributor_street_2',
         'contributor_city', 'contributor_state', 'contributor_zip'],
        dropna=False
    ):
        donor_id = donor_key_to_id.get(donor_key)
        if not donor_id:
            continue
        address_id = addr_dim_id.get(_akey(street_1, street_2, city, state, zip_code))
        if address_id is None:
            continue
        donor_address_rows.append((donor_id, address_id))

    execute_values(cur,
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s",
        donor_address_rows, page_size=5000)
    conn.commit()

    # Build donor-address lookup.
    cur.execute("""
        SELECT da.donor_address_id, da.donor_id,
               COALESCE(a.street_1,''), COALESCE(a.street_2,''),
               COALESCE(a.city,''), COALESCE(a.state_code,''), COALESCE(a.zip_code,'')
        FROM donor_addresses da
        JOIN addresses a ON a.address_id = da.address_id
    """)
    for row in cur.fetchall():
        addr_key_to_id[(row[1], row[2], row[3], row[4], row[5], row[6])] = row[0]
    logger.info(f"  donor_addresses: {_count(cur, 'donor_addresses'):,} ({time.time()-start:.1f}s)")
    return addr_key_to_id


def link_employer_locations(
    conn: Any,
    cur: Any,
    addr_dim_id: dict,
    get_employer_id,
    employer_locations: list[dict] | None = None,
) -> None:
    """Step 8: link each employer's default location."""
    logger.info("\n-- 8/8 Linking employer locations --")
    start = time.time()

    employer_rows = []
    locations = employer_locations
    if locations is None:
        locations = load_employer_locations()
    for location in locations:
        if not location["is_primary"]:
            continue
        emp_id = get_employer_id(location["employer_name"])
        if not emp_id:
            raise RuntimeError(
                "employer location has no exact employer match: "
                f"{location['employer_name']!r}"
            )
        address_id = addr_dim_id.get(_akey(
            location["employer_address"], None,
            location["employer_city"], location["employer_state"],
            location["employer_zip"],
        ))
        if address_id is None:
            raise RuntimeError(
                "employer location has no exact address match: "
                f"{location['employer_name']!r}"
            )
        employer_rows.append((int(emp_id), int(address_id)))

    if employer_rows:
        execute_values(cur, """
            UPDATE employers e
            SET address_id = v.address_id
            FROM (VALUES %s) AS v(employer_id, address_id)
            WHERE e.employer_id = v.employer_id
        """,
        employer_rows,
        template="(%s::int, %s::int)",
        page_size=5000)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM employers WHERE address_id IS NOT NULL")
    linked = cur.fetchone()[0]
    logger.info(f"  employers with location: {linked:,} ({time.time()-start:.1f}s)")

    # Remove unused addresses.
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM donor_employments de WHERE de.address_id = a.address_id)
    """)
    n_orphan = cur.rowcount
    conn.commit()
    if n_orphan:
        logger.info(f"  pruned {n_orphan:,} orphan addresses")
