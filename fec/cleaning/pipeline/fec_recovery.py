"""cleaning/pipeline/fec_recovery.py — recover unknown addresses from FEC.gov.

The last residue of donors whose street_1 is not a usable address — and who
have no clean filing of their own anywhere in OUR pull (the three PACs) — can
still be resolved: the same person almost always gave a full address when they
contributed to OTHER committees nationwide. This step asks the FEC API for a
donor's other individual contributions, takes their dominant real street, runs
it through the SAME street normaliser the rest of the pipeline uses, and fills
it in.

Network calls are gated and cached:
  - runs only when an out_dir is given (the production run) AND FEC_API_KEY is
    set — so tests calling clean() with out_dir=None never hit the network;
  - results (hits AND misses) are cached in <out_dir>/fec_address_cache.json,
    so a donor is asked for exactly once across all future runs.

Safety: only INDIVIDUAL rows with a non-usable street are touched; the recovered
street is applied only when the FEC address sits in the SAME city as our row (or
our city is blank) — a different city means a possible namesake or second home,
which we leave for manual review rather than guess.
"""
from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from fec.log import get_logger
from fec.resolve.pipeline.cache import Cache
from fec.cleaning.addresses import _normalize_street
from fec.cleaning.pipeline.address_fixes import _is_usable_street

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"
WORKERS = 5
CACHE_NAME = "fec_address_cache.json"


def _fetch_one(name: str, state: str, key: str) -> dict:
    """Query the FEC API for one donor's dominant real street address.

    Returns {'street','city','zip','n','total','method':'fec_api'} on a hit,
    or {'street':'','method':'fec_not_found'} when no usable address is found.
    Returns None on a transient error so the donor is retried next run."""
    import requests
    try:
        resp = requests.get(f"{FEC_BASE}/schedules/schedule_a/", params={
            "api_key": key,
            "contributor_name": name,
            "contributor_state": state,
            "is_individual": "true",
            "per_page": 30,
            "sort": "-contribution_receipt_date",
        }, timeout=20)
        if resp.status_code == 429:
            time.sleep(5)
            return None
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
    except Exception:
        return None

    addrs: Counter = Counter()
    for r in results:
        s1 = (r.get("contributor_street_1") or "").strip()
        if _is_usable_street(pd.Series([s1])).iloc[0]:
            city = (r.get("contributor_city") or "").strip().upper()
            zc = (r.get("contributor_zip") or "").strip()[:5]
            addrs[(s1.upper(), city, zc)] += 1
    if not addrs:
        return {"street": "", "method": "fec_not_found"}
    (street, city, zc), n = addrs.most_common(1)[0]
    return {"street": street, "city": city, "zip": zc,
            "n": n, "total": len(results), "method": "fec_api"}


def recover_addresses_from_fec(df: pd.DataFrame, out_dir: str | None) -> int:
    """Fill non-usable INDIVIDUAL streets from the donor's FEC-wide history.

    Cached + network-gated (see module docstring). Returns rows filled."""
    if not out_dir or "entity_type" not in df.columns:
        return 0

    usable = _is_usable_street(df["contributor_street_1"])
    target = (
        (df["entity_type"] == "INDIVIDUAL")
        & ~usable
        & df["contributor_name"].fillna("").str.strip().ne("")
        & df["contributor_state"].fillna("").str.strip().ne("")
    )
    if not target.any():
        return 0

    cache = Cache(os.path.join(out_dir, CACHE_NAME))

    # Distinct (name, state) to resolve.
    people = (
        df.loc[target, ["contributor_name", "contributor_state"]]
        .drop_duplicates().itertuples(index=False, name=None)
    )
    todo = [(n, s) for (n, s) in people if cache.get(f"{n}|{s}") is None]

    # Key must be in the environment already (clean.py loads .env at startup).
    # We deliberately do NOT load .env here: that would make a no-key unit test
    # silently start hitting the network.
    key = os.environ.get("FEC_API_KEY", "").strip()
    if todo and not key:
        logger.info(f"    ⚠ FEC_API_KEY not set — skipping FEC address "
                    f"recovery ({len(todo)} unknown donors)")
    elif todo:
        logger.info(f"    FEC address recovery: querying {len(todo)} donors "
                    f"({len(cache)} cached)")
        done = 0
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futs = {pool.submit(_fetch_one, n, s, key): (n, s) for (n, s) in todo}
            for fut in as_completed(futs):
                n, s = futs[fut]
                res = fut.result()
                if res is not None:
                    cache.put(f"{n}|{s}", res)
                done += 1
                if done % 25 == 0:
                    cache.save()
        cache.save()

    # Apply from cache (works even with no key, for already-cached donors).
    n_filled = 0
    for idx in df[target].index:
        name = df.at[idx, "contributor_name"]
        state = df.at[idx, "contributor_state"]
        hit = cache.get(f"{name}|{state}")
        if not hit or not hit.get("street"):
            continue
        street = _normalize_street(hit["street"])
        if not street or pd.isna(street):
            continue
        our_city = str(df.at[idx, "contributor_city"] or "").strip().upper()
        fec_city = str(hit.get("city") or "").strip().upper()
        # Same-city guard: a different city is a possible namesake / 2nd home.
        if our_city and fec_city and our_city != fec_city:
            continue
        df.at[idx, "contributor_street_1"] = street
        if not our_city and fec_city:
            df.at[idx, "contributor_city"] = fec_city
        if "contributor_zip" in df.columns:
            if not str(df.at[idx, "contributor_zip"] or "").strip() and hit.get("zip"):
                df.at[idx, "contributor_zip"] = hit["zip"]
        n_filled += 1

    return n_filled
