"""Recover missing streets from FEC filings, preserving filed descriptions and units."""

from __future__ import annotations

import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from fec.cleaning.addresses.compare import street_and_unit
from fec.cleaning.addresses.fixes.recovery import _is_usable_street, _text
from fec.cleaning.addresses.street_text import normalize_street
from fec.log import get_logger
from fec.resolve.cache import Cache

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"
WORKERS = 5
CACHE_NAME = "fec_address_cache.json"


# Find a donor's most common usable FEC address.
# Return an empty street for a miss, or None for a temporary failure.
def _fetch_one(name: str, state: str, key: str) -> dict | None:
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
            unit = (record.get("contributor_street_2") or "").strip().upper()
            addrs[(s1.upper(), unit, city, zip_code)] += 1
    if not addrs:
        return {"street": "", "method": "fec_not_found"}
    (street, unit, city, zip_code), count = addrs.most_common(1)[0]
    return {
        "street": street,
        "unit": unit,
        "city": city,
        "zip": zip_code,
        "n": count,
        "total": len(results),
        "method": "fec_api",
    }


# Only missing streets qualify; an institution or description is filed evidence.
def _recovery_target(df: pd.DataFrame) -> pd.Series:
    target = (
        (df["entity_type"] == "INDIVIDUAL")
        & df["contributor_street_1"].fillna("").str.strip().eq("")
        & df["contributor_name"].fillna("").str.strip().ne("")
        & df["contributor_state"].fillna("").str.strip().ne("")
    )
    if "_care_of_blanked" in df:
        target &= ~df["_care_of_blanked"].fillna(False).astype(bool)
    return target


# distinct (name, state) pairs among target rows not yet cached
def _unknown_people(df: pd.DataFrame, target: pd.Series, cache) -> list[tuple]:
    if "contributor_street_2" in df:
        target = target & df["contributor_street_2"].fillna("").str.strip().eq("")
    people = (
        df.loc[target, ["contributor_name", "contributor_state"]]
        .drop_duplicates()
        .itertuples(index=False, name=None)
    )
    return [
        (name, state) for name, state in people if cache.get(f"{name}|{state}") is None
    ]


# fetch and cache addresses for every unknown donor, in parallel
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


# fill target rows' street/city/zip from cached FEC results
def _apply_cached_addresses(df: pd.DataFrame, target: pd.Series, cache) -> int:
    n_filled = 0
    for idx in df[target].index:
        name = df.at[idx, "contributor_name"]
        state = df.at[idx, "contributor_state"]
        hit = cache.get(f"{name}|{state}")
        if not hit or not hit.get("street"):
            continue
        street = normalize_street(hit["street"])
        if not street or pd.isna(street):
            continue
        unit = _text(df.at[idx, "contributor_street_2"]) if "contributor_street_2" in df else ""
        inline_unit = street_and_unit(street, "")[1]
        source_unit = street_and_unit("", _text(hit.get("unit")))[1]
        if inline_unit and source_unit and inline_unit != source_unit:
            continue
        if unit and street_and_unit("", unit)[1] != (source_unit or inline_unit):
            continue
        our_city = _text(df.at[idx, "contributor_city"]).upper()
        fec_city = _text(hit.get("city")).upper()
        # same-city guard: a different city may be a namesake or a second home
        if our_city and fec_city and our_city != fec_city:
            continue
        our_zip = _text(df.at[idx, "contributor_zip"])[:5]
        if our_zip and hit.get("zip") and our_zip != _text(hit["zip"])[:5]:
            continue
        df.at[idx, "contributor_street_1"] = street
        if not our_city and fec_city:
            df.at[idx, "contributor_city"] = fec_city
        if not our_zip and hit.get("zip"):
            df.at[idx, "contributor_zip"] = hit["zip"]
        n_filled += 1

    return n_filled


# Fill missing streets from FEC history while preserving filed descriptions.
def recover_addresses_from_fec(df: pd.DataFrame, cache_dir: str | None) -> int:
    if not cache_dir:
        return 0

    target = _recovery_target(df)
    if not target.any():
        return 0

    cache = Cache(os.path.join(cache_dir, CACHE_NAME))
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
