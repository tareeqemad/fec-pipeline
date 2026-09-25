"""Last-resort street recovery from the donor's own filings to OTHER committees via the FEC API; network-gated (needs out_dir AND FEC_API_KEY, so tests never hit the network), hits and misses cached forever in fec_address_cache.json, same-city guard."""

from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from fec.cleaning.addresses import _normalize_street
from fec.cleaning.pipeline.address_fixes import _is_usable_street
from fec.log import get_logger
from fec.resolve.pipeline.cache import Cache

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"
WORKERS = 5
CACHE_NAME = "fec_address_cache.json"


def _fetch_one(name: str, state: str, key: str) -> dict | None:
    """Query the FEC API for one donor's dominant real street; miss = {'street': ''}, None = transient error so the donor is retried next run."""
    try:
        resp = requests.get(
            f"{FEC_BASE}/schedules/schedule_a/",
            params={
                "api_key": key,
                "contributor_name": name,
                "contributor_state": state,
                "is_individual": "true",
                "per_page": 30,
                "sort": "-contribution_receipt_date",
            },
            timeout=20,
        )
        if resp.status_code == 429:
            time.sleep(5)
            return None
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
    except Exception:
        return None

    addrs: Counter = Counter()
    for record in results:
        s1 = (record.get("contributor_street_1") or "").strip()
        if _is_usable_street(pd.Series([s1])).iloc[0]:
            city = (record.get("contributor_city") or "").strip().upper()
            zip_code = (record.get("contributor_zip") or "").strip()[:5]
            addrs[(s1.upper(), city, zip_code)] += 1
    if not addrs:
        return {"street": "", "method": "fec_not_found"}
    (street, city, zip_code), count = addrs.most_common(1)[0]
    return {
        "street": street,
        "city": city,
        "zip": zip_code,
        "n": count,
        "total": len(results),
        "method": "fec_api",
    }


def _recovery_target(df: pd.DataFrame) -> pd.Series:
    usable = _is_usable_street(df["contributor_street_1"])
    return (
        (df["entity_type"] == "INDIVIDUAL")
        & ~usable
        & df["contributor_name"].fillna("").str.strip().ne("")
        & df["contributor_state"].fillna("").str.strip().ne("")
    )


def _unknown_people(df: pd.DataFrame, target: pd.Series, cache) -> list[tuple]:
    people = (
        df.loc[target, ["contributor_name", "contributor_state"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    return [
        (name, state) for name, state in people if cache.get(f"{name}|{state}") is None
    ]


def _fetch_unknown_people(todo: list[tuple], key: str, cache) -> None:
    logger.info(
        f"    FEC address recovery: querying {len(todo)} donors ({len(cache)} cached)"
    )
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(_fetch_one, name, state, key): (name, state)
            for name, state in todo
        }
        for done, future in enumerate(as_completed(futures), 1):
            name, state = futures[future]
            result = future.result()
            if result is not None:
                cache.put(f"{name}|{state}", result)
            if done % 25 == 0:
                cache.save()
    cache.save()


def _apply_cached_addresses(df: pd.DataFrame, target: pd.Series, cache) -> int:
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
        # same-city guard: a different city may be a namesake or a second home
        if our_city and fec_city and our_city != fec_city:
            continue
        df.at[idx, "contributor_street_1"] = street
        if not our_city and fec_city:
            df.at[idx, "contributor_city"] = fec_city
        if not str(df.at[idx, "contributor_zip"] or "").strip() and hit.get("zip"):
            df.at[idx, "contributor_zip"] = hit["zip"]
        n_filled += 1

    return n_filled


def recover_addresses_from_fec(df: pd.DataFrame, out_dir: str | None) -> int:
    """Fill unusable streets from the donor's cached FEC-wide history."""
    if not out_dir:
        return 0

    target = _recovery_target(df)
    if not target.any():
        return 0

    cache = Cache(os.path.join(out_dir, CACHE_NAME))
    todo = _unknown_people(df, target, cache)
    key = os.environ.get("FEC_API_KEY", "").strip()
    if todo and not key:
        logger.info(
            f"    WARNING: FEC_API_KEY not set - skipping FEC address "
            f"recovery ({len(todo)} unknown donors)"
        )
    elif todo:
        _fetch_unknown_people(todo, key, cache)

    return _apply_cached_addresses(df, target, cache)
