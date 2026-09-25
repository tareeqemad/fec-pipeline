"""Shared address loading and links."""
from __future__ import annotations

import time
from typing import Any

import pandas as pd
from psycopg2.extras import execute_values

from fec.env import EMPLOYER_LOCATIONS_CSV
from fec.log import get_logger
from fec.resolve.pipeline.locations import PUBLISHABLE_ADDRESS_TRUST

from ._base import _count, to_float_or_none, to_native

logger = get_logger(__name__)


# treat an empty string address part as null
def _blank_to_none(value):
    """A missing address part is NULL, never '' (the CSV is read with keep_default_na=False)."""
    value = to_native(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


# load resolved, publishable employer locations from disk
def load_employer_locations(frame: pd.DataFrame | None = None) -> list[dict]:
    """Read resolved employer locations; empty address parts become None."""
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
        # a home-based business is published as its town only (no street); a row
        # with neither a street nor a town is an employer without a public office
        if (_blank_to_none(row.get("employer_address")) is None
                and _blank_to_none(row.get("employer_city")) is None):
            continue
        if row.get("address_trust") not in PUBLISHABLE_ADDRESS_TRUST:
            continue
        locations.append({
            "employer_name": row["employer_name"],
            "employer_address": _blank_to_none(row.get("employer_address")),
            "employer_city": _blank_to_none(row.get("employer_city")),
            "employer_state": _blank_to_none(row.get("employer_state")),
            "employer_zip": _blank_to_none(row.get("employer_zip")),
            "employer_latitude": row.get("employer_latitude"),
            "employer_longitude": row.get("employer_longitude"),
            "is_primary": str(row.get("is_primary")).lower() == "true",
        })

    grouped: dict[str, list[dict]] = {}
    for location in locations:
        grouped.setdefault(location["employer_name"], []).append(location)
    for employer_locations in grouped.values():
        if not any(location["is_primary"] for location in employer_locations):
            employer_locations[0]["is_primary"] = True
    return locations


# treat an empty string value as null
def _empty_to_none(value):
    """Native value with '' stored as NULL; _akey already maps both to ''."""
    value = to_native(value)
    return None if value == '' else value


# normalized address key tuple used to match rows
def _akey(st1, st2, city, state, z):
    """Normalized address tuple, '' for empty parts, matching the COALESCE(col,'') shape read back from addresses."""
    return (to_native(st1) or '', to_native(st2) or '', to_native(city) or '',
            to_native(state) or '', to_native(z) or '')


# load the shared donor/employer address dimension table
def load_address_dimension(
    conn: Any,
    cur: Any,
    df: pd.DataFrame,
    employer_locations: list[dict] | None = None,
) -> dict:
    """Step 4: load donor and employer addresses."""
    logger.info("\n-- 4/8 Loading addresses (shared dimension) --")
    start = time.time()

    addr_dim = _address_rows(df, _employer_locations(employer_locations))
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


# use given employer locations, or load them from disk
def _employer_locations(employer_locations: list[dict] | None) -> list[dict]:
    """The given employer locations, else the ones on disk."""
    if employer_locations is None:
        return load_employer_locations()
    return employer_locations


# one row per distinct donor or employer address
def _address_rows(df: pd.DataFrame, locations: list[dict]) -> dict:
    """One row per distinct donor or employer address, with coordinates."""
    addr_dim = {}
    # Donor addresses.
    for (street_1, street_2, city, state, zip_code), group in df.groupby(
        ['contributor_street_1', 'contributor_street_2', 'contributor_city',
         'contributor_state', 'contributor_zip'], dropna=False
    ):
        first_row = group.iloc[0]
        _add_address(addr_dim, street_1, street_2, city, state, zip_code,
                     to_float_or_none(first_row.get('latitude')),
                     to_float_or_none(first_row.get('longitude')))

    for location in locations:
        _add_address(
            addr_dim,
            location["employer_address"], None,
            location["employer_city"], location["employer_state"],
            location["employer_zip"],
            to_float_or_none(location.get("employer_latitude")),
            to_float_or_none(location.get("employer_longitude")),
        )
    return addr_dim


# add an address, or backfill its missing coordinates
def _add_address(addr_dim: dict, st1, st2, city, state, z, lat, lng) -> None:
    """Add one address, or fill a stored address's missing coordinates."""
    key = _akey(st1, st2, city, state, z)
    entry = addr_dim.get(key)
    if entry is None:
        # '' and NULL share one key (_akey), so store the NULL form.
        addr_dim[key] = [_empty_to_none(st1), _empty_to_none(st2),
                         _empty_to_none(city), _empty_to_none(state),
                         _empty_to_none(z), lat, lng]
    elif entry[5] is None and lat is not None:  # Backfill coordinates.
        entry[5], entry[6] = lat, lng


# load donor-address link rows and their lookup map
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


# link each employer to its default location
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

    employer_rows = _primary_location_rows(
        _employer_locations(employer_locations), addr_dim_id, get_employer_id,
    )
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
    _prune_orphan_addresses(conn, cur)


# employer/address id pairs for each primary location
def _primary_location_rows(locations, addr_dim_id: dict, get_employer_id) -> list[tuple]:
    """(employer_id, address_id) for each employer's primary location."""
    employer_rows = []
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
    return employer_rows


# delete addresses no donor, employer, or employment uses
def _prune_orphan_addresses(conn, cur) -> None:
    """Delete addresses no donor, employer or employment uses."""
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
