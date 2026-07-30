"""Shared addresses dimension, donor_addresses links, and employer HQ linking."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import EMPLOYERS_CSV
from fec.log import get_logger

from ._base import _count, to_float_or_none, to_native

logger = get_logger(__name__)


def _load_employer_hqs() -> dict:
    """employer_name -> HQ address dict for employers.csv rows with a resolved HQ; {} if the file is missing."""
    if not EMPLOYERS_CSV.exists():
        return {}
    employers_df = pd.read_csv(EMPLOYERS_CSV, dtype=str, keep_default_na=False, na_values=[""])
    employers_df = employers_df[
        employers_df["employer_address"].notna() & (employers_df["employer_address"] != "")]
    out = {}
    for row in employers_df.to_dict("records"):
        out[row["employer_name"]] = {
            "address": row.get("employer_address"), "city": row.get("employer_city"),
            "state": row.get("employer_state"), "zip": row.get("employer_zip"),
            "lat": row.get("employer_latitude"), "lng": row.get("employer_longitude"),
        }
    return out


def _akey(st1, st2, city, state, z):
    """Normalized address tuple, '' for empty parts, matching the COALESCE(col,'') shape read back from addresses."""
    return (to_native(st1) or '', to_native(st2) or '', to_native(city) or '',
            to_native(state) or '', to_native(z) or '')


def load_address_dimension(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 4: shared addresses dimension (donor homes and employer HQs dedup here); returns _akey tuple -> address_id."""
    logger.info("\n-- 4/8 Loading addresses (shared dimension) --")
    start = time.time()

    addr_dim = {}

    def _add_addr(st1, st2, city, state, z, lat, lng):
        key = _akey(st1, st2, city, state, z)
        entry = addr_dim.get(key)
        if entry is None:
            addr_dim[key] = [to_native(st1), to_native(st2), to_native(city),
                             to_native(state), to_native(z), lat, lng]
        elif entry[5] is None and lat is not None:  # backfill coords from any source
            entry[5], entry[6] = lat, lng

    # 4a. Donor residences; street_2 keeps different units in one building separate.
    for (street_1, street_2, city, state, zip_code), group in df.groupby(
        ['contributor_street_1', 'contributor_street_2', 'contributor_city',
         'contributor_state', 'contributor_zip'], dropna=False
    ):
        first_row = group.iloc[0]
        _add_addr(street_1, street_2, city, state, zip_code,
                  to_float_or_none(first_row.get('latitude')),
                  to_float_or_none(first_row.get('longitude')))

    # 4b. Employer HQs, one per company, no street_2. A self-employed donor's
    # "company" address is their home, already added in 4a (employers.csv excludes them).
    employer_hqs = _load_employer_hqs()
    for hq in employer_hqs.values():
        _add_addr(hq['address'], None, hq['city'], hq['state'], hq['zip'],
                  to_float_or_none(hq.get('lat')), to_float_or_none(hq.get('lng')))

    addr_keys = list(addr_dim.keys())
    execute_values(cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(addr_dim[key]) for key in addr_keys], page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)")
    conn.commit()

    # COALESCE so NULLs hash equal to the _akey ''
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

    # Join back to addresses for the (donor_id, address tuple) -> donor_address_id map
    # that drives contributions.donor_address_id.
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


def link_employer_hqs(conn: Any, cur: Any, addr_dim_id: dict, get_employer_id) -> None:
    """Step 8: point employers.address_id at the shared addresses row (first resolvable HQ per employer wins), then prune unreferenced addresses."""
    logger.info("\n-- 8/8 Linking employer HQ addresses --")
    start = time.time()

    emp_hq_rows = []

    # One HQ per company from employers.csv; keyed by company name, so it covers
    # former employers too.
    seen_emp = set()
    for name, hq in _load_employer_hqs().items():
        emp_id = get_employer_id(name)
        if not emp_id or emp_id in seen_emp:
            continue
        # No street_2 -- matches how step 4b registered the HQ.
        address_id = addr_dim_id.get(_akey(
            hq.get('address'), None, hq.get('city'), hq.get('state'), hq.get('zip')))
        if address_id is None:
            continue
        seen_emp.add(emp_id)
        emp_hq_rows.append((int(emp_id), int(address_id)))

    if emp_hq_rows:
        execute_values(cur, """
            UPDATE employers e
            SET address_id = v.address_id
            FROM (VALUES %s) AS v(employer_id, address_id)
            WHERE e.employer_id = v.employer_id
        """,
        emp_hq_rows,
        template="(%s::int, %s::int)",
        page_size=5000)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM employers WHERE address_id IS NOT NULL")
    n_with_hq = cur.fetchone()[0]
    logger.info(f"  employers with HQ: {n_with_hq:,} ({time.time()-start:.1f}s)")

    # Prune address rows nobody references so the shared dimension has no dangling rows.
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
    """)
    n_orphan = cur.rowcount
    conn.commit()
    if n_orphan:
        logger.info(f"  pruned {n_orphan:,} orphan addresses")
