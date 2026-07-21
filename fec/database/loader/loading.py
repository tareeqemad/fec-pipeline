"""database/loader/loading.py — load the cleaned CSV + reference data into the normalized tables."""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

import pandas as pd

try:
    from psycopg2.extras import execute_values
except ImportError:
    raise ImportError("psycopg2 not installed. Run: pip install psycopg2-binary")

from fec.env import CLEANED_CSV, PROJECT_ROOT, EMPLOYERS_CSV


def _load_employer_hqs() -> dict:
    """employer_name -> dict(address, city, state, zip, lat, lng) for every
    company in employers.csv that has a resolved HQ. Empty if the file is
    missing (the cleaned CSV no longer carries per-row employer addresses)."""
    if not EMPLOYERS_CSV.exists():
        return {}
    e = pd.read_csv(EMPLOYERS_CSV, dtype=str, keep_default_na=False, na_values=[""])
    e = e[e["employer_address"].notna() & (e["employer_address"] != "")]
    out = {}
    for r in e.to_dict("records"):
        out[r["employer_name"]] = {
            "address": r.get("employer_address"), "city": r.get("employer_city"),
            "state": r.get("employer_state"), "zip": r.get("employer_zip"),
            "lat": r.get("employer_latitude"), "lng": r.get("employer_longitude"),
        }
    return out

from fec.log import get_logger

logger = get_logger(__name__)

from ._base import (
    NON_EMPLOYER_STATUSES,
    _count,
    to_float_or_none,
    to_int_or_none,
    to_native,
)
from fec.cleaning.employer_synonyms import EMPLOYER_SYNONYMS, canonical_key


# Committee identities (names, logos, fundraising totals) live in
# data/database/committees.csv — see fec.committees. No hard-coded seed here.


def load_lookups(conn: Any, cur: Any) -> None:
    """Load occupation_categories and committees."""
    logger.info("── Loading lookups ──")

    # Occupation categories from data
    df = pd.read_csv(CLEANED_CSV, usecols=['occupation_category'], dtype=str)
    cats = sorted(df['occupation_category'].dropna().unique())
    rows = [(cat,) for cat in cats]
    execute_values(cur, "INSERT INTO occupation_categories (name) VALUES %s ON CONFLICT DO NOTHING",
                   rows, page_size=50)
    conn.commit()
    logger.info("  ✓ occupation_categories: %d", _count(cur, 'occupation_categories'))

    # Committees — from data/database/committees.csv (single source of truth).
    from fec.committees import load_committees
    for c in load_committees():
        num = c.get('committee_number')
        vals = (num, c['committee_name'], c['committee_short'],
                c['raised'], c['spent'], c['irs_990_link'], c['logo_path'])
        if num:
            cur.execute(
                "INSERT INTO committees (committee_number, committee_name, committee_short, raised, spent, irs_990_link, logo_path) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (committee_number) DO UPDATE SET "
                "committee_name=EXCLUDED.committee_name, committee_short=EXCLUDED.committee_short, "
                "raised=EXCLUDED.raised, spent=EXCLUDED.spent, irs_990_link=EXCLUDED.irs_990_link, "
                "logo_path=EXCLUDED.logo_path",
                vals)
        else:
            # Non-FEC orgs (NULL committee_number) — insert only if name not exists
            cur.execute(
                "INSERT INTO committees (committee_number, committee_name, committee_short, raised, spent, irs_990_link, logo_path) "
                "SELECT %s, %s, %s, %s, %s, %s, %s "
                "WHERE NOT EXISTS (SELECT 1 FROM committees WHERE committee_name = %s)",
                vals + (c['committee_name'],))

    conn.commit()
    logger.info("  ✓ committees: %d", _count(cur, 'committees'))


# ── shared helpers for the per-step loaders ───────────────────────────

def _akey(st1, st2, city, state, z):
    """Normalized address tuple — '' for empty parts, matching the
    COALESCE(col,'') shape the load steps read back from `addresses`."""
    return (to_native(st1) or '', to_native(st2) or '', to_native(city) or '',
            to_native(state) or '', to_native(z) or '')


def _build_ck_index(emp_name_to_id: dict) -> dict[str, int]:
    """canonical_key(name) → employer_id for every employer in the map
    (first name wins per key). The ONE builder for every canonical-key
    index in this module — link_previous_employers' routing index and
    _make_employer_resolver's fallback index both come from here."""
    index: dict[str, int] = {}
    for name, eid in emp_name_to_id.items():
        k = canonical_key(name)
        if k and k not in index:
            index[k] = eid
    return index


def _make_employer_resolver(emp_name_to_id: dict):
    """Build the `_get_employer_id` lookup over the COMPLETE employer map.

    Called after link_previous_employers: the canonical_key index is built
    once here (so the fallback lookup is O(1)) and never refreshed — safe
    because no later load step inserts employers.
    """
    ck_index = _build_ck_index(emp_name_to_id)

    def _get_employer_id(emp_name):
        """Look up employer ID by name. Returns None for status words.

        Tries: exact → synonym rewrite → canonical_key fallback.
        Catches "WHATSAPP" (raw FEC value from a retiree's previous_employer)
        and routes it to the existing "WHATSAPP LLC" employer row.
        """
        if pd.isna(emp_name):
            return None
        emp = str(emp_name).strip()
        if emp.upper() in NON_EMPLOYER_STATUSES:
            return None
        eid = emp_name_to_id.get(emp) or emp_name_to_id.get(emp.upper())
        if eid:
            return eid
        synonym = EMPLOYER_SYNONYMS.get(emp.upper())
        if synonym:
            eid = emp_name_to_id.get(synonym) or emp_name_to_id.get(synonym.upper())
            if eid:
                return eid
        ck = canonical_key(synonym or emp)
        return ck_index.get(ck) if ck else None

    return _get_employer_id


# ── per-step loaders — each takes the id-maps it needs and returns the
#    id-maps it produces; load_all (below) threads them together. ──────

def load_donors(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 1 — one donors row per donor_key. Returns donor_key → donor_id."""
    logger.info("\n── 1/8 Loading donors ──")
    t = time.time()

    donor_rows = []
    for dk, group in df.groupby('donor_key'):
        # Names are already canonical per donor_key (clean.py wrote one clean
        # first/last to every row), so any row gives the same pair — just read
        # the latest. No name logic here: the loader only stores what the
        # cleaned CSV already settled.
        latest = group.sort_values('contribution_receipt_date', ascending=False).iloc[0]
        entity = to_native(latest['entity_type'])
        first = to_native(latest['contributor_first_name'])
        last = to_native(latest['contributor_last_name'])
        # For committees: first/last are NULL, store full name in last_name
        if not last and entity in ('COMMITTEE/PAC', 'ORGANIZATION'):
            last = to_native(latest['contributor_name'])
        donor_rows.append((dk, entity, first, last))

    execute_values(cur,
        "INSERT INTO donors (donor_key, entity_type, first_name, last_name) "
        "VALUES %s",
        donor_rows, page_size=5000)
    conn.commit()

    # Get assigned IDs
    cur.execute("SELECT donor_id, donor_key FROM donors")
    donor_key_to_id = {r[1]: r[0] for r in cur.fetchall()}
    logger.info(f"  ✓ donors: {len(donor_key_to_id):,} ({time.time()-t:.1f}s)")
    return donor_key_to_id


def load_employers(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 2 — unique employers of INDIVIDUAL donors. Returns name → employer_id."""
    logger.info("\n── 2/8 Loading employers ──")
    t = time.time()

    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    # contributor_employer is already normalized by the cleaning pipeline:
    #   LLC/INC/LLP stripped, & → AND, known synonyms unified.
    emp_series = indiv['contributor_employer'].dropna().str.strip()
    emp_series = emp_series[~emp_series.str.upper().isin(NON_EMPLOYER_STATUSES)]
    emp_unique = emp_series.unique()

    employer_rows = [(name,) for name in emp_unique]

    execute_values(cur,
        "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        employer_rows, page_size=5000)
    conn.commit()

    cur.execute("SELECT employer_id, name FROM employers")
    emp_name_to_id = {r[1]: r[0] for r in cur.fetchall()}
    logger.info(f"  ✓ employers: {_count(cur, 'employers'):,} ({time.time()-t:.1f}s)")
    return emp_name_to_id


def link_previous_employers(conn: Any, cur: Any, df: pd.DataFrame,
                            emp_name_to_id: dict) -> dict[str, int]:
    """Step 3 — ensure the employers table includes every `previous_employer`.

    The `previous_employer` column (populated by resolve.py for RETIRED
    donors) may reference companies that don't otherwise appear as a
    contributor_employer. Insert those into employers so
    donor_employments.previous_employer_id can reference them.
    No UPDATE on donors — previous_employer_id is on donor_employments
    and is set during that load step.

    Grows emp_name_to_id IN PLACE when it inserts new employer rows (so it
    must run before _make_employer_resolver). Returns donor_key → previous
    employer_id.
    """
    logger.info("\n── 3/8 Linking previous employers ──")

    donor_prev_employer_id: dict[str, int] = {}  # donor_key -> employer_id
    if 'previous_employer' not in df.columns:
        return donor_prev_employer_id
    prev_emp = df[df['previous_employer'].notna() & (df['previous_employer'] != '')]
    if len(prev_emp) == 0:
        return donor_prev_employer_id

    prev_latest = prev_emp.sort_values('contribution_receipt_date', ascending=False) \
        .drop_duplicates('donor_key', keep='first')

    # Build a canonical_key index from CURRENT employers so we can
    # route previous_employer values to an existing entity when they
    # share the same canonical form. Without this, "KIRKLAND & ELLIS"
    # (from a retiree's previous job) becomes a SECOND employer row
    # next to "KIRKLAND & ELLIS LLP" (current employees), even though
    # they're the same firm.
    ck_to_emp_id = _build_ck_index(emp_name_to_id)

    # Collect unique prev_employer names not yet routable to an
    # existing employer via canonical_key. Filter out status words.
    prev_names = set()
    for name in prev_latest['previous_employer']:
        s = str(name).strip()
        if not s:
            continue
        # Synonym dict first (catches WHATSAPP → WHATSAPP LLC, etc.)
        s_up = s.upper()
        s = EMPLOYER_SYNONYMS.get(s_up, s)
        if s.upper() in NON_EMPLOYER_STATUSES:
            continue
        # If an existing employer has the same canonical_key, reuse it
        k = canonical_key(s)
        if k and k in ck_to_emp_id:
            continue
        if s not in emp_name_to_id and s.upper() not in emp_name_to_id:
            prev_names.add(s)
    if prev_names:
        execute_values(cur,
            "INSERT INTO employers (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(n,) for n in prev_names], page_size=1000)
        conn.commit()
        cur.execute("SELECT employer_id, name FROM employers")
        emp_name_to_id.clear()
        emp_name_to_id.update({r[1]: r[0] for r in cur.fetchall()})
        # Refresh the canonical-key index after the inserts
        ck_to_emp_id = _build_ck_index(emp_name_to_id)

    # Build donor_key -> previous_employer_id map. Route via
    # canonical_key so e.g. "KIRKLAND & ELLIS" (raw) finds the
    # canonical "KIRKLAND & ELLIS LLP" entity.
    for _, row in prev_latest.iterrows():
        dk = row['donor_key']
        raw = str(row['previous_employer']).strip()
        normalized = EMPLOYER_SYNONYMS.get(raw.upper(), raw)
        emp_id = (emp_name_to_id.get(normalized)
                  or emp_name_to_id.get(normalized.upper())
                  or ck_to_emp_id.get(canonical_key(normalized)))
        if dk and emp_id:
            donor_prev_employer_id[dk] = emp_id
    if donor_prev_employer_id:
        logger.info(f"  ✓ previous_employer: {len(donor_prev_employer_id):,} donors mapped")
    return donor_prev_employer_id


def load_address_dimension(conn: Any, cur: Any, df: pd.DataFrame) -> dict:
    """Step 4 — the shared `addresses` dimension.

    ONE source of truth for every physical address: donor residences AND
    employer HQs dedup into `addresses`, so a self-employed donor's home and
    "company" address collapse to a single geocoded row. donor_addresses and
    employers.address_id both point here.

    Returns the normalized address tuple (_akey shape) → addresses.address_id.
    """
    logger.info("\n── 4/8 Loading addresses (shared dimension) ──")
    t = time.time()

    addr_dim = {}  # (st1,st2,city,state,zip) '' for empty → [st1,st2,city,state,zip,lat,lng]

    def _add_addr(st1, st2, city, state, z, lat, lng):
        k = _akey(st1, st2, city, state, z)
        e = addr_dim.get(k)
        if e is None:
            addr_dim[k] = [to_native(st1), to_native(st2), to_native(city),
                           to_native(state), to_native(z), lat, lng]
        elif e[5] is None and lat is not None:      # backfill coords from any source
            e[5], e[6] = lat, lng

    # 4a. Donor residences — unique address tuples across all donors. street_2
    # is included so different units in one building stay separate.
    for (st1, st2, city, state, z5), grp in df.groupby(
        ['contributor_street_1', 'contributor_street_2', 'contributor_city',
         'contributor_state', 'contributor_zip'], dropna=False
    ):
        r0 = grp.iloc[0]
        _add_addr(st1, st2, city, state, z5,
                  to_float_or_none(r0.get('latitude')), to_float_or_none(r0.get('longitude')))

    # 4b. Employer HQs — one per company, from the normalized employers.csv
    # dimension. Corporate HQ has no street_2. A self-employed donor's "company"
    # address is their home, already added in 4a (employers.csv excludes them).
    employer_hqs = _load_employer_hqs()
    for hq in employer_hqs.values():
        _add_addr(hq['address'], None, hq['city'], hq['state'], hq['zip'],
                  to_float_or_none(hq.get('lat')), to_float_or_none(hq.get('lng')))

    addr_keys = list(addr_dim.keys())
    execute_values(cur,
        """INSERT INTO addresses
           (street_1, street_2, city, state_code, zip_code, latitude, longitude)
           VALUES %s""",
        [tuple(addr_dim[k]) for k in addr_keys], page_size=5000,
        template="(%s, %s, %s, %s, %s, %s::float8, %s::float8)")
    conn.commit()

    # norm-key → addresses.address_id  (COALESCE so NULLs hash equal to the _akey '')
    cur.execute("""SELECT address_id, COALESCE(street_1,''), COALESCE(street_2,''),
                          COALESCE(city,''), COALESCE(state_code,''), COALESCE(zip_code,'')
                   FROM addresses""")
    addr_dim_id = {}
    for r in cur.fetchall():
        addr_dim_id[(r[1], r[2], r[3], r[4], r[5])] = r[0]
    logger.info(f"  ✓ addresses: {_count(cur, 'addresses'):,} ({time.time()-t:.1f}s)")
    return addr_dim_id


def load_donor_addresses(conn: Any, cur: Any, df: pd.DataFrame,
                         donor_key_to_id: dict, addr_dim_id: dict) -> dict:
    """Step 5 — the donor_addresses link, one row per (donor, address).

    Returns (donor_id, st1, st2, city, state, zip) → donor_address_id.
    """
    logger.info("\n── 5/8 Loading donor addresses ──")
    t = time.time()

    addr_key_to_id = {}  # (donor_id, st1, st2, city, state, zip) → donor_addresses.donor_address_id
    da_rows = []
    for (dk, st1, st2, city, state, z5), grp in df.groupby(
        ['donor_key', 'contributor_street_1', 'contributor_street_2',
         'contributor_city', 'contributor_state', 'contributor_zip'],
        dropna=False
    ):
        donor_id = donor_key_to_id.get(dk)
        if not donor_id: continue
        address_id = addr_dim_id.get(_akey(st1, st2, city, state, z5))
        if address_id is None: continue
        da_rows.append((donor_id, address_id))

    execute_values(cur,
        "INSERT INTO donor_addresses (donor_id, address_id) VALUES %s",
        da_rows, page_size=5000)
    conn.commit()

    # Rebuild (donor_id, address tuple) → donor_addresses.donor_address_id by joining
    # back to `addresses` (the address columns live there now). Drives contributions.donor_address_id.
    cur.execute("""
        SELECT da.donor_address_id, da.donor_id,
               COALESCE(a.street_1,''), COALESCE(a.street_2,''),
               COALESCE(a.city,''), COALESCE(a.state_code,''), COALESCE(a.zip_code,'')
        FROM donor_addresses da
        JOIN addresses a ON a.address_id = da.address_id
    """)
    for r in cur.fetchall():
        addr_key_to_id[(r[1], r[2], r[3], r[4], r[5], r[6])] = r[0]
    logger.info(f"  ✓ donor_addresses: {_count(cur, 'donor_addresses'):,} ({time.time()-t:.1f}s)")
    return addr_key_to_id


def load_employments(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                     occ_cat_map: dict, donor_prev_employer_id: dict,
                     get_employer_id) -> dict:
    """Step 6 — donor_employments (one row per donor/employer/occupation).

    Returns (donor_id, employer_id, occupation) → donor_employment_id.
    """
    logger.info("\n── 6/8 Loading donor employments ──")
    t = time.time()

    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    empl_rows = []
    seen_empl = set()  # (donor_id, employer_id, occupation) dedup

    # Group by (donor, employer, occupation) — preserves career
    # progression. A donor who was ASSOCIATE then PARTNER at the same
    # firm gets two rows, each with its own date range (populated
    # from contributions after this step). This is the schema's
    # UNIQUE key; the date range comes from contributions' min/max
    # receipt_date per row.
    agg_specs = {
        'occupation_category': ('occupation_category', 'first'),
    }
    if 'employer_status' in indiv.columns:
        agg_specs['employer_status'] = ('employer_status', 'first')

    empl_agg = (
        indiv.groupby(['donor_key', 'contributor_employer', 'contributor_occupation'],
                      dropna=False)
        .agg(**agg_specs)
        .reset_index()
    )

    for _, row in empl_agg.iterrows():
        dk = row['donor_key']
        emp_val = row['contributor_employer']
        occ = to_native(row['contributor_occupation'])

        donor_id = donor_key_to_id.get(dk)
        if not donor_id: continue

        emp_id = get_employer_id(emp_val)

        dedup_key = (donor_id, emp_id, occ or '')
        if dedup_key in seen_empl:
            continue
        seen_empl.add(dedup_key)

        occ_cat = to_native(row.get('occupation_category'))
        occ_cat_id = occ_cat_map.get(occ_cat)

        emp_status = to_native(row.get('employer_status')) if 'employer_status' in row.index else None
        prev_emp_id = donor_prev_employer_id.get(dk)

        empl_rows.append((
            donor_id, emp_id, occ, occ_cat_id,
            emp_status, prev_emp_id,
        ))

    execute_values(cur,
        """INSERT INTO donor_employments
           (donor_id, employer_id, occupation, occupation_category_id,
            employer_status, previous_employer_id)
           VALUES %s ON CONFLICT DO NOTHING""",
        empl_rows, page_size=5000)
    conn.commit()

    # Build employment lookup — by (donor_id, employer_id, occupation)
    # since the schema preserves career progression (multiple occupations per
    # donor+employer).
    cur.execute("SELECT donor_employment_id, donor_id, employer_id, occupation FROM donor_employments")
    empl_donor_emp_to_id = {}
    for r in cur.fetchall():
        empl_donor_emp_to_id[(r[1], r[2], r[3])] = r[0]
    logger.info(f"  ✓ donor_employments: {_count(cur, 'donor_employments'):,} ({time.time()-t:.1f}s)")
    return empl_donor_emp_to_id


def load_contributions(conn: Any, cur: Any, df: pd.DataFrame, donor_key_to_id: dict,
                       comm_map: dict, addr_key_to_id: dict,
                       empl_donor_emp_to_id: dict, get_employer_id) -> None:
    """Step 7 — the contributions fact table (vectorized)."""
    logger.info("\n── 7/8 Loading contributions ──")
    t = time.time()

    # Map FK columns using vectorized .map() instead of 183K dict lookups
    df['_donor_id'] = df['donor_key'].map(donor_key_to_id)
    df['_comm_id'] = df['recipient_committee'].map(comm_map)

    has_donor = df['_donor_id'].notna()
    has_both = has_donor & df['_comm_id'].notna()
    valid = df[has_both].copy()
    skipped_no_donor = int((~has_donor).sum())
    skipped_no_committee = int(has_donor.sum()) - len(valid)

    # A row that maps to no committee or no donor would silently vanish from
    # the fact table — refuse to load instead.
    if skipped_no_committee > 0:
        unmapped = sorted(set(
            df.loc[has_donor & df['_comm_id'].isna(), 'recipient_committee'].fillna('<blank>')
        ))
        shown = ", ".join(repr(u) for u in unmapped[:10])
        more = f" (+{len(unmapped) - 10} more)" if len(unmapped) > 10 else ""
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {skipped_no_committee} contribution row(s) reference "
            f"recipient_committee value(s) with no committees-table match: {shown}{more} — "
            f"add them to data/database/committees.csv")
    if skipped_no_donor > 0:
        sample = df.loc[~has_donor, 'sub_id'].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {skipped_no_donor} contribution row(s) have a donor_key "
            f"that mapped to no donors row — sample sub_ids: {sample}")

    # Pre-compute address keys → address_id. Key shape must match what
    # load_donor_addresses put into addr_key_to_id:
    # (donor_id, street_1, street_2, city, state, zip_code)
    valid['_st1'] = valid['contributor_street_1'].fillna('').astype(str)
    valid['_st2'] = valid['contributor_street_2'].fillna('').astype(str)
    valid['_city'] = valid['contributor_city'].fillna('').astype(str)
    valid['_state'] = valid['contributor_state'].fillna('').astype(str)
    valid['_z5'] = valid['contributor_zip'].fillna('').astype(str)
    valid['_addr_id'] = [
        addr_key_to_id.get((int(did), s1, s2, c, st, z))
        for did, s1, s2, c, st, z in zip(
            valid['_donor_id'], valid['_st1'], valid['_st2'],
            valid['_city'], valid['_state'], valid['_z5']
        )
    ]

    # Pre-compute employment keys → employment_id. Same resolver as the
    # load_employments step (exact → synonym → canonical_key), so the
    # composite lookup key below can never diverge from what was inserted.
    valid['_emp_id'] = valid['contributor_employer'].map(get_employer_id)
    # Lookup employment_id by (donor_id, employer_id, occupation) — the schema
    # preserves career progression via the composite key.
    valid['_occ_key'] = valid['contributor_occupation'].where(
        valid['contributor_occupation'].notna(), None,
    )
    valid['_empl_id'] = [
        empl_donor_emp_to_id.get((int(did),
                                  None if pd.isna(eid) else int(eid),
                                  None if (occ is None or (isinstance(occ, float) and pd.isna(occ))) else occ))
        for did, eid, occ in zip(
            valid['_donor_id'], valid['_emp_id'], valid['_occ_key']
        )
    ]

    # An INDIVIDUAL row that resolved an employer but found no matching
    # donor_employments row means the composite (donor, employer, occupation)
    # keys diverged between the two build steps — a silent NULL here would
    # detach the contribution from its employment.
    bad_empl = ((valid['entity_type'] == 'INDIVIDUAL')
                & valid['_emp_id'].notna() & valid['_empl_id'].isna())
    if bad_empl.any():
        sample = valid.loc[bad_empl, 'sub_id'].head(10).tolist()
        raise RuntimeError(
            f"{CLEANED_CSV.name}: {int(bad_empl.sum())} INDIVIDUAL contribution row(s) "
            f"resolved an employer_id but no donor_employment row — "
            f"sample sub_ids: {sample}")

    # Amounts were coerced to numeric once at read time (loader/__init__).
    # A NaN here is unparseable garbage in the CSV — refuse to load it as 0.
    amt_na = valid['contribution_receipt_amount'].isna()
    if amt_na.any():
        sample = valid.loc[amt_na, 'sub_id'].head(10).tolist()
        raise ValueError(
            f"{CLEANED_CSV.name}: {int(amt_na.sum())} row(s) with missing/unparseable "
            f"contribution_receipt_amount — sample sub_ids: {sample}")

    # Pre-convert date columns (receipt_date is nullable — coerce stands)
    valid['_dt'] = pd.to_datetime(valid['contribution_receipt_date'], errors='coerce')
    valid['_cycle'] = pd.to_numeric(valid['two_year_transaction_period'], errors='coerce')

    # Build tuples from columns — convert to native Python types for psycopg2
    cont_rows = list(zip(
        [int(s) for s in valid['sub_id']],            # BIGINT PK (read as string → exact int)
        [str(t) for t in valid['transaction_id']],
        [to_int_or_none(d) for d in valid['_donor_id']],
        [to_int_or_none(c) for c in valid['_comm_id']],
        [to_int_or_none(a) for a in valid['_addr_id']],
        [to_int_or_none(e) for e in valid['_empl_id']],
        [to_float_or_none(a) for a in valid['contribution_receipt_amount']],
        [d.date() if pd.notna(d) else None for d in valid['_dt']],
        [to_int_or_none(c) for c in valid['_cycle']],
    ))

    execute_values(cur,
        """INSERT INTO contributions
           (sub_id, transaction_id, donor_id, committee_id, donor_address_id, donor_employment_id,
            amount, receipt_date, election_cycle)
           VALUES %s""",
        cont_rows, page_size=5000)
    conn.commit()

    # Clean up temp columns
    df.drop(columns=['_donor_id', '_comm_id'], inplace=True, errors='ignore')

    logger.info("  ✓ contributions: %s (%0.1fs)", f"{_count(cur, 'contributions'):,}", time.time()-t)


def link_employer_hqs(conn: Any, cur: Any, addr_dim_id: dict, get_employer_id) -> None:
    """Step 8 — employer HQ addresses: point employers.address_id at the
    shared `addresses` dimension (the HQ row was already inserted in step 4),
    then prune address rows nobody references.

    For each employer we pick ONE HQ (first row whose address resolves wins).
    Rows where the address belongs to a retiree's PREVIOUS_EMPLOYER
    (employer_status=retired/not_employed) carry the address for the
    previous_employer entity, not the current.
    """
    logger.info("\n── 8/8 Linking employer HQ addresses ──")
    t = time.time()

    emp_hq_rows = []  # (emp_id, address_id)

    # One HQ per company, straight from employers.csv (covers both current and
    # former employers — the dimension is keyed by company name, so no retiree
    # previous_employer special-casing is needed here).
    seen_emp = set()
    for name, hq in _load_employer_hqs().items():
        emp_id = get_employer_id(name)
        if not emp_id or emp_id in seen_emp:
            continue
        # Corporate HQ has no street_2 — matches how step 4b registered it.
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
    logger.info(f"  ✓ employers with HQ: {n_with_hq:,} ({time.time()-t:.1f}s)")

    # Prune address rows referenced by nobody (e.g. an employer HQ from a
    # retiree's filing whose entity never got linked) so the shared dimension
    # stays free of dangling rows.
    cur.execute("""
        DELETE FROM addresses a
        WHERE NOT EXISTS (SELECT 1 FROM donor_addresses d WHERE d.address_id = a.address_id)
          AND NOT EXISTS (SELECT 1 FROM employers e WHERE e.address_id = a.address_id)
    """)
    n_orphan = cur.rowcount
    conn.commit()
    if n_orphan:
        logger.info(f"  ✓ pruned {n_orphan:,} orphan addresses")


def load_all(conn: Any, cur: Any, df: pd.DataFrame) -> None:
    """Load all data from the cleaned CSV into the normalized tables.

    Thin orchestrator over the 8 per-step loaders above — each step returns
    the id-map(s) later steps need, and the maps are threaded explicitly here.
    """
    # Build lookup maps
    cur.execute("SELECT occupation_category_id, name FROM occupation_categories")
    occ_cat_map = {r[1]: r[0] for r in cur.fetchall()}

    # Map recipient_committee name (committee_short) → committees.committee_id.
    # The cleaned CSV carries the name (AIPAC/DMFI/UDP), not the FEC number.
    cur.execute("SELECT committee_id, committee_short FROM committees WHERE committee_short IS NOT NULL")
    comm_map = {r[1]: r[0] for r in cur.fetchall()}

    donor_key_to_id = load_donors(conn, cur, df)
    emp_name_to_id = load_employers(conn, cur, df)
    # May grow emp_name_to_id in place — must run before the resolver is built.
    donor_prev_employer_id = link_previous_employers(conn, cur, df, emp_name_to_id)
    get_employer_id = _make_employer_resolver(emp_name_to_id)

    addr_dim_id = load_address_dimension(conn, cur, df)
    addr_key_to_id = load_donor_addresses(conn, cur, df, donor_key_to_id, addr_dim_id)
    empl_donor_emp_to_id = load_employments(conn, cur, df, donor_key_to_id, occ_cat_map,
                                            donor_prev_employer_id, get_employer_id)
    load_contributions(conn, cur, df, donor_key_to_id, comm_map,
                       addr_key_to_id, empl_donor_emp_to_id, get_employer_id)

    # NOTE: the schema no longer stores denormalized "current" pointers
    # on donors. v_donor_current_address and v_donor_current_employment
    # views compute them on read via DISTINCT ON, so nothing to update here.

    # NOTE: first/last "seen" dates are NOT stored on the history tables.
    # They're derived on read from contributions (MIN/MAX receipt_date) by the
    # v_donor_newest_* views — one source of truth for dates, no cached column.

    link_employer_hqs(conn, cur, addr_dim_id, get_employer_id)


def refresh_materialized_views(conn: Any, cur: Any) -> None:
    """Refresh materialized views (schema v1.2).

    schema.sql already creates `mv_donor_profile` + its indexes at
    schema-create time (empty, since contributions are not loaded yet).
    After data loading, we just REFRESH it — CONCURRENTLY so queries
    keep working during refresh (requires the unique index that
    schema.sql creates).
    """
    logger.info(f"\n── Refreshing materialized views ──")
    t = time.time()

    cur.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_donor_profile")
    conn.commit()
    cur.execute("ANALYZE mv_donor_profile")
    logger.info(f"  ✓ mv_donor_profile: {_count(cur, 'mv_donor_profile'):,} rows ({time.time()-t:.1f}s)")


REF_TABLES = [
    # (table_name, csv_filename, columns)
    ("us_states", "us_states.csv",
     ["state_fips", "code", "name"]),
    ("zcta_state_rel", "zcta_state_rel.csv",
     ["zcta5", "state_fips"]),
    ("zip_centroids", "zip_centroids.csv",
     ["zip", "lat", "lng", "source"]),
    # NOTE: leaders.csv and key_accomplices.csv are NOT in REF_TABLES.
    # They're loaded by load_leadership() / load_key_accomplices() because
    # each row needs match_or_create_donor + donor_addresses upsert before
    # the actual table INSERT.
]


def load_reference_tables(conn: Any, cur: Any) -> None:
    """Load reference tables from data/database/*.csv files."""
    db_dir = PROJECT_ROOT / "data" / "database"
    if not db_dir.exists():
        logger.info(f"\n  ⚠ {db_dir} not found — skipping reference tables")
        return

    logger.info(f"\n── Loading reference tables ──")

    for table_name, csv_file, columns in REF_TABLES:
        csv_path = db_dir / csv_file
        if not csv_path.exists():
            logger.info(f"  ⚠ {csv_file} not found — skipping {table_name}")
            continue

        t = time.time()
        df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

        # Use only columns that exist in both CSV and table definition
        use_cols = [c for c in columns if c in df.columns]
        if not use_cols:
            logger.warning(f"  ⚠ No matching columns in {csv_file}")
            continue

        df = df[use_cols]

        # Replace empty strings with None (empty cell → SQL NULL for nullable cols)
        df = df.replace('', None)

        # Drop fully-empty rows (trailing blank lines)
        df = df.dropna(how='all')

        # A row missing a NOT NULL column is corrupt reference data — refuse
        # to load rather than silently dropping it.
        not_null_cols = {
            'us_states': ['state_fips', 'code', 'name'],
            'zcta_state_rel': ['zcta5', 'state_fips'],
            'zip_centroids': ['zip', 'lat', 'lng'],
        }
        for col in not_null_cols.get(table_name, []):
            if col not in df.columns:
                raise ValueError(
                    f"{csv_file}: required column {col!r} missing "
                    f"(NOT NULL in {table_name})")
            missing = df[col].isna()
            if missing.any():
                # +2 → 1-based line numbers counting the header row
                lines = [int(i) + 2 for i in df.index[missing][:10]]
                raise ValueError(
                    f"{csv_file}: {int(missing.sum())} row(s) missing required "
                    f"column {col!r} (NOT NULL in {table_name}) — "
                    f"sample CSV line number(s): {lines}")

        # Build INSERT — a failure here propagates (bad reference data must
        # abort the load, not degrade it).
        cols_str = ", ".join(use_cols)
        rows = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False, name=None)]

        if rows:
            execute_values(cur,
                f"INSERT INTO {table_name} ({cols_str}) VALUES %s ON CONFLICT DO NOTHING",
                rows, page_size=5000)
            conn.commit()

        logger.info(f"  ✓ {table_name}: {_count(cur, table_name):,} ({time.time()-t:.1f}s)")


def _reset_id_sequence(conn: Any, cur: Any, table: str) -> None:
    """Reset a table's SERIAL PK sequence to MAX(pk) so future inserts don't collide.

    The PK column is now per-table (leader_id / accomplice_id / …), so we
    discover whichever column owns a serial sequence instead of assuming 'id'.
    """
    try:
        cur.execute(
            """
            SELECT a.attname
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped
              AND pg_get_serial_sequence(c.relname, a.attname) IS NOT NULL
            LIMIT 1
            """,
            (table,),
        )
        row = cur.fetchone()
        if not row:
            return
        col = row[0]
        cur.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', '{col}'), "
            f"COALESCE(MAX({col}), 1)) FROM {table}"
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _load_donor_linked_csv(conn: Any, cur: Any, csv_filename: str,
                           table: str, insert_row) -> None:
    """Load a CSV whose rows must each link to a donor (leaders / key_accomplices).

    Unlike the bulk-INSERT path for other reference tables, every row is
    resolved to a donor first — an existing one (matched by donor_key or
    name) or a fresh donor row auto-created for people who never donated
    to FEC. The row's address goes to `donor_addresses`; then `insert_row`
    does the table-specific INSERT.

    insert_row(cur, row, donor_id) — INSERT one row into `table`.
    """
    from fec.database.leadership_matcher import (
        match_or_create_donor,
        upsert_donor_address,
        upsert_leader_employment,
    )

    csv_path = PROJECT_ROOT / "data" / "database" / csv_filename
    if not csv_path.exists():
        logger.info(f"  ⚠ {csv_path.name} not found — skipping {table}")
        return

    t = time.time()
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)

    match_counts: dict[str, int] = defaultdict(int)
    inserted = 0
    skipped = 0

    # Column aliases: each file prefixes its person-columns by what the person
    # is — leaders.csv uses leader_*, key_accomplices.csv uses accomplice_*.
    # _col() reads whichever is present, so both files work through one loader.
    def _col(row, field):
        return (row.get(f"leader_{field}") or row.get(f"accomplice_{field}") or "").strip()

    for _, row in df.iterrows():
        name = _col(row, "name")
        if not name:
            skipped += 1
            continue

        first = _col(row, "first_name")
        last  = _col(row, "last_name")
        # first/last are optional in the CSV — derive them from the
        # "LAST, FIRST" name when missing so match_or_create_donor still works.
        if (not first or not last) and "," in name:
            last_part, _, first_part = name.partition(",")
            last = last or last_part.strip()
            first = first or first_part.strip()
        city  = _col(row, "city")
        state = _col(row, "state")

        donor_id, method = match_or_create_donor(cur, name, first, last, city, state)
        match_counts[method] += 1

        # Portrait → donor_images (1:1 per person). The image is a property of
        # the donor, not the role, so the 5 overlap people collapse to one row.
        img = (row.get("image_path") or "").strip()
        if img:
            cur.execute(
                "INSERT INTO donor_images (donor_id, image_path) VALUES (%s, %s) "
                "ON CONFLICT (donor_id) DO UPDATE SET image_path = EXCLUDED.image_path",
                (donor_id, img),
            )

        # Address → donor_addresses (upsert_donor_address dedups via check-then-insert)
        street_1 = _col(row, "street_1")
        street_2 = _col(row, "street_2")
        if street_1 or city:
            # lat and lng are parsed in SEPARATE guards so one bad value never
            # nulls its valid twin. Blank is valid editorial state (silent);
            # non-blank garbage warns loudly — these files are hand-maintained.
            lat_s = (row.get("address_lat") or "").strip()
            lng_s = (row.get("address_lng") or "").strip()
            latitude = longitude = None
            if lat_s:
                try:
                    latitude = float(lat_s)
                except ValueError:
                    logger.warning("  ⚠ %s: %s — unparseable address_lat %r, storing NULL",
                                   csv_filename, name, lat_s)
            if lng_s:
                try:
                    longitude = float(lng_s)
                except ValueError:
                    logger.warning("  ⚠ %s: %s — unparseable address_lng %r, storing NULL",
                                   csv_filename, name, lng_s)
            upsert_donor_address(
                cur, donor_id,
                street_1=street_1, street_2=street_2,
                city=city, state=state,
                zip_5=_col(row, "zip"),
                latitude=latitude, longitude=longitude,
            )

        # Employment ONLY from leaders.csv's `leader_employer` — a real, already
        # canonicalised (all-caps) company. key_accomplices.csv's
        # `accomplice_employer` is an editorial description (a mirror of the
        # subtitle, e.g. "Media Oligarch", "Founder of Oracle") and a few
        # proper-case company names ("Meta") — NOT a clean company field. Feeding
        # it here inserted ~54 junk/duplicate employer rows verbatim (a case-dup
        # "Meta" next to the contribution-derived "META", plus pure descriptions),
        # which then split companies in v_company and the employer filter. The DB
        # must receive clean employer names, so accomplices contribute none here —
        # those who donated already carry their real FEC employment; the rest are
        # described by their subtitle, not an employment row.
        # Only fires for people who never donated; no-op when the cell is blank.
        upsert_leader_employment(cur, donor_id, (row.get("leader_employer") or "").strip())

        insert_row(cur, row, donor_id)
        inserted += 1

    conn.commit()
    _reset_id_sequence(conn, cur, table)
    logger.info(
        f"  ✓ {table}: {inserted} rows ({skipped} skipped), "
        f"match methods: {dict(match_counts)} ({time.time()-t:.1f}s)"
    )


def load_leadership(conn: Any, cur: Any) -> None:
    """Load leaders.csv → leaders (donor_id) + leader_committees (M:N junction)."""
    # Valid committee ids — a CSV id pointing at a non-existent committee is
    # skipped (with a warning) rather than aborting the load on the FK.
    cur.execute("SELECT committee_id FROM committees")
    valid_committees = {r[0] for r in cur.fetchall()}

    def _insert(cur, row, donor_id):
        # committee_ids arrives as a Postgres array literal like "{1,2}".
        # Blank cells are valid editorial state; a NON-BLANK unparseable token
        # warns loudly (hand-maintained file — warn, don't raise).
        raw = (row.get("committee_ids") or "").strip().strip("{}")
        wanted = []
        for tok in raw.split(","):
            tok = tok.strip()
            if not tok:
                continue
            if tok.lstrip("-").isdigit():
                wanted.append(int(tok))
            else:
                logger.warning("  ⚠ leaders.csv: %s — unparseable committee_ids token %r, skipping it",
                               (row.get("leader_name") or "").strip(), tok)
        kept = [c for c in wanted if c in valid_committees]
        dropped = [c for c in wanted if c not in valid_committees]
        if dropped:
            logger.warning("  ⚠ leader donor_id=%s: skipping unknown committee_ids %s", donor_id, dropped)

        # One leaders row per donor; DO UPDATE (no-op) lets RETURNING fetch the
        # leader_id whether the row was just inserted or already existed.
        cur.execute(
            """
            INSERT INTO leaders (donor_id) VALUES (%s)
            ON CONFLICT (donor_id) DO UPDATE SET donor_id = EXCLUDED.donor_id
            RETURNING leader_id
            """,
            (donor_id,),
        )
        leader_id = cur.fetchone()[0]

        # Reset this leader's committee links, then re-add (idempotent on re-run).
        cur.execute("DELETE FROM leader_committees WHERE leader_id = %s", (leader_id,))
        if kept:
            execute_values(
                cur,
                "INSERT INTO leader_committees (leader_id, committee_id) VALUES %s",
                [(leader_id, cid) for cid in kept],
            )

    _load_donor_linked_csv(conn, cur, "leaders.csv", "leaders", _insert)


def load_key_accomplices(conn: Any, cur: Any) -> None:
    """Load key_accomplices.csv → key_accomplices table (donor_id FK + card fields)."""
    def _txt(row, col):
        v = (row.get(col) or "").strip()
        return v or None

    def _insert(cur, row, donor_id):
        # Blank cells are valid editorial state; NON-BLANK garbage warns
        # loudly (hand-maintained file — warn, don't raise).
        who = (row.get("accomplice_name") or "").strip()
        committee_raw = (row.get("committee_id") or "").strip()
        committee_id = None
        if committee_raw:
            if committee_raw.isdigit():
                committee_id = int(committee_raw)
            else:
                logger.warning("  ⚠ key_accomplices.csv: %s — unparseable committee_id %r, storing NULL",
                               who, committee_raw)
        sort_raw = (row.get("display_order") or "").strip()
        display_order = 0
        if sort_raw:
            if sort_raw.lstrip("-").isdigit():
                display_order = int(sort_raw)
            else:
                logger.warning("  ⚠ key_accomplices.csv: %s — unparseable display_order %r, using 0",
                               who, sort_raw)
        cur.execute(
            """
            INSERT INTO key_accomplices
                (donor_id, sign, subtitle, body_text,
                 committee_id, display_order)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (donor_id) DO UPDATE SET
                sign          = EXCLUDED.sign,
                subtitle      = EXCLUDED.subtitle,
                body_text     = EXCLUDED.body_text,
                committee_id  = EXCLUDED.committee_id,
                display_order = EXCLUDED.display_order
            """,
            (
                donor_id,
                _txt(row, "sign"),
                _txt(row, "subtitle"),
                _txt(row, "body_text"),
                committee_id,
                display_order,
            ),
        )

    _load_donor_linked_csv(conn, cur, "key_accomplices.csv", "key_accomplices", _insert)
