"""Retirees' previous employers from the FEC API."""
import csv
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from fec.cleaning.employer_status import (
    classify_employer_status,
)
from fec.cleaning.manual_overrides import CLEAR_PREVIOUS_EMPLOYER
from fec.cleaning.previous_employer import normalize_previous_employer_value
from fec.env import MANUAL_EMPLOYER_OVERRIDES_CSV, RAW_CSV
from fec.io import cell_text
from fec.log import get_logger
from fec.resolve.pipeline.previous_cache import cache_entry, previous_cache_key, retired_donors

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"


FEC_PAGE_SIZE = 100


# beyond 2,000 filings the search gives up
FEC_MAX_PAGES = 20


# wait before the single 429 retry
RATE_LIMIT_WAIT_SECONDS = 5


# sentinel: hourly FEC quota used up
RATE_LIMITED = object()


# split a value into uppercase alphanumeric words
def _words(value) -> list[str]:
    return re.findall(r"[A-Z0-9]+", cell_text(value).upper())


# split a name into last, first, middle
def _name_parts(value) -> tuple[str, str, str]:
    text = cell_text(value)
    if "," in text:
        last, given = text.split(",", 1)
        last_words = _words(last)
        given_words = _words(given)
    else:
        words = _words(text)
        last_words, given_words = words[-1:], words[:-1]

    last = " ".join(last_words)
    first = given_words[0] if given_words else ""
    middle = given_words[1][0] if len(given_words) > 1 else ""
    return last, first, middle


# AND, &, / or + joins people
_JOINT_GIVEN_RE = re.compile(r"\bAND\b|&|/|\+")


# true if a filing names two people
def _joint_name(value) -> bool:
    text = cell_text(value).upper()
    given = text.split(",", 1)[1] if "," in text else text
    return bool(_JOINT_GIVEN_RE.search(given))


# drop cached FEC answers from joint filings
def drop_joint_source_entries(prev_cache) -> int:
    data = getattr(prev_cache, "data", prev_cache)
    joint = [
        key for key, entry in data.items()
        if isinstance(entry, dict) and str(entry.get("method", "")).startswith("fec_api")
        and _joint_name(entry.get("source_name"))
    ]
    for key in joint:
        data.pop(key, None)
    return len(joint)


# first 5 digits of a ZIP code
def _zip5(value) -> str:
    digits = "".join(re.findall(r"\d", cell_text(value)))
    return digits[:5]


# record matches donor's name, state and ZIP
def _same_fec_donor(person: dict, record: dict) -> bool:
    if _joint_name(record.get("contributor_name")) and not _joint_name(person.get("name")):
        return False
    expected = _name_parts(person.get("name"))
    reported = _name_parts(record.get("contributor_name"))
    if not all(expected[:2]) or expected[:2] != reported[:2]:
        return False
    if expected[2] and reported[2] and expected[2] != reported[2]:
        return False

    expected_state = cell_text(person.get("state")).upper()
    reported_state = cell_text(record.get("contributor_state")).upper()
    if expected_state and reported_state and expected_state != reported_state:
        return False

    # ZIP must be the donor's own, unshared
    reported_zip = _zip5(record.get("contributor_zip"))
    return (bool(reported_zip) and reported_zip in _person_zips(person)
            and reported_zip not in (person.get("shared_zips") or ()))


# ZIP5s a donor files from, plus latest
def _person_zips(person: dict) -> set[str]:
    zips = {_zip5(value) for value in person.get("zips") or ()}
    zips.add(_zip5(person.get("zip")))
    return zips - {""}


# every ZIP5 each donor files from
def _donor_zips(df: pd.DataFrame) -> dict[str, set[str]]:
    people = df[df["entity_type"] == "INDIVIDUAL"]
    zips = people["contributor_zip"].fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:5]
    frame = pd.DataFrame({"key": people["donor_key"], "zip": zips})
    frame = frame[frame["zip"].str.len() == 5]
    return frame.groupby("key")["zip"].agg(set).to_dict()


# ZIPs each donor shares with a namesake
def _shared_zips(df: pd.DataFrame) -> dict[str, set[str]]:
    people = df[(df["entity_type"] == "INDIVIDUAL") & ~df["contributor_name"].map(_joint_name)]
    given = people["contributor_first_name"].fillna("").astype(str).str.upper().str.split()
    # two whole given names mean a couple
    people = people[given.map(lambda words: all(len(w.strip(".")) <= 1 for w in words[1:]))]
    first = given[people.index].str[0].fillna("")
    frame = pd.DataFrame({
        "key": people["donor_key"],
        "name": people["contributor_last_name"].fillna("").astype(str).str.upper() + "|" + first,
        "zip": people["contributor_zip"].fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:5],
    }).drop_duplicates()
    frame = frame[(frame["zip"].str.len() == 5) & ~frame["name"].str.endswith("|")]
    keys_per_place = frame.groupby(["name", "zip"])["key"].transform("nunique")
    shared = frame[keys_per_place > 1]
    return shared.groupby("key")["zip"].agg(set).to_dict()


# per donor, filed employers overrides cleared
def cleared_employers(df: pd.DataFrame) -> dict[str, set[str]]:
    if not (MANUAL_EMPLOYER_OVERRIDES_CSV.exists() and RAW_CSV.exists()):
        return {}
    with MANUAL_EMPLOYER_OVERRIDES_CSV.open(encoding="utf-8", newline="") as handle:
        cleared = {
            (row.get("sub_id") or "").strip() for row in csv.DictReader(handle)
            if (row.get("contributor_employer") or "").strip().upper() == CLEAR_PREVIOUS_EMPLOYER
        }
    if not cleared:
        return {}
    raw = pd.read_csv(RAW_CSV, usecols=["sub_id", "contributor_employer"], dtype=str, keep_default_na=False)
    raw = raw[raw["sub_id"].isin(cleared)]
    keys = df.assign(sub_id=df["sub_id"].astype(str)).set_index("sub_id")["donor_key"]
    out: dict[str, set[str]] = {}
    for sub_id, employer in zip(raw["sub_id"], raw["contributor_employer"]):
        name = normalize_previous_employer_value(employer).upper()
        if name and sub_id in keys.index:
            out.setdefault(str(keys.loc[sub_id]), set()).add(name)
    return out


# drop cached FEC answers not proven donor's
def drop_unproven_source_entries(prev_cache, df: pd.DataFrame, cleared: dict | None = None) -> int:
    data = getattr(prev_cache, "data", prev_cache)
    donor_zips, shared_zips = _donor_zips(df), _shared_zips(df)
    unproven = []
    for key, entry in data.items():
        if not (isinstance(entry, dict) and entry.get("method") == "fec_api"):
            continue
        source_zip = _zip5(entry.get("source_zip"))
        donor_key = key.split(":", 1)[-1]
        zips = donor_zips.get(donor_key)
        if source_zip and zips and (source_zip not in zips or source_zip in shared_zips.get(donor_key, ())):
            unproven.append(key)
        elif str(entry.get("employer", "")).upper() in (cleared or {}).get(donor_key, ()):
            unproven.append(key)
        # no longer a company, so search again
        elif not normalize_previous_employer_value(entry.get("employer", "")):
            unproven.append(key)
    for key in unproven:
        data.pop(key, None)
    return len(unproven)


# unresolved retired donors to search, largest first
def _pending_fec_searches(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> list[dict]:
    _, latest_retired = retired_donors(df)
    tier_keys = set(donor_totals["donor_key"])
    donor_zips, shared_zips = _donor_zips(df), _shared_zips(df)
    donor_info = {
        row["donor_key"]: {
            "prev_key": previous_cache_key(row["donor_key"]),
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
            "city": row.get("contributor_city", ""),
            "zip": row.get("contributor_zip", ""),
            "zips": donor_zips.get(row["donor_key"], set()),
            "shared_zips": shared_zips.get(row["donor_key"], set()),
        }
        for _, row in latest_retired.iterrows()
    }

    todo_cache_keys, todo_donors = set(), set()
    for donor_key, info in donor_info.items():
        cache_key = info["prev_key"]
        if (
            donor_key in tier_keys
            and cache_key not in todo_cache_keys
            and _fec_search_due(prev_cache.get(cache_key))
        ):
            todo_donors.add(donor_key)
            todo_cache_keys.add(cache_key)

    totals = donor_totals.set_index("donor_key")["donor_total"].to_dict()
    searches = [{**donor_info[key], "total": totals.get(key, 0)} for key in todo_donors]
    searches.sort(key=lambda entry: -entry["total"])
    return searches


# true if no answer or partial not-found
def _fec_search_due(cached: dict | None) -> bool:
    if cached is None:
        return True
    return cached.get("method") == "fec_api_not_found" and not cached.get("all_pages")


# yield FEC filings; None means search incomplete
def _fec_filings(person: dict, fec_key: str, request_get):
    params = {
        "api_key": fec_key,
        "contributor_name": person["name"],
        "contributor_state": person["state"],
        "per_page": FEC_PAGE_SIZE,
        "sort": "-contribution_receipt_date",
        "is_individual": "true",
    }
    for _page in range(FEC_MAX_PAGES):
        response = request_get(f"{FEC_BASE}/schedules/schedule_a/", params=params, timeout=15)
        if response.status_code == 429:
            time.sleep(RATE_LIMIT_WAIT_SECONDS)
            response = request_get(f"{FEC_BASE}/schedules/schedule_a/", params=params, timeout=15)
            if response.status_code == 429:
                yield RATE_LIMITED
                return
        if response.status_code != 200:
            yield None
            return
        body = response.json()
        results = body.get("results", [])
        yield from results
        last = (body.get("pagination") or {}).get("last_indexes")
        if len(results) < FEC_PAGE_SIZE or not last:
            return
        params = {**params, **last}


# find one donor's latest active-employment FEC filing
def _fetch_fec_previous_employer(person: dict, fec_key: str, request_get):
    try:
        for record in _fec_filings(person, fec_key, request_get):
            if record is RATE_LIMITED:
                return None, RATE_LIMITED
            if record is None:
                return None, None
            if not _same_fec_donor(person, record):
                continue
            if (
                classify_employer_status(
                    record.get("contributor_employer", ""),
                    record.get("contributor_occupation", ""),
                )
                != "active"
            ):
                continue
            entry = cache_entry(
                record.get("contributor_employer", ""),
                state=record.get("contributor_state", person["state"]),
                method="fec_api",
                source_date=record.get("contribution_receipt_date", ""),
                source_name=record.get("contributor_name", ""),
                source_city=record.get("contributor_city", ""),
                source_zip=record.get("contributor_zip", ""),
            )
            if entry.get("employer") and entry["employer"].upper() not in person.get("cleared_employers", ()):
                return person["prev_key"], entry

        return person["prev_key"], {
            "employer": "",
            "method": "fec_api_not_found",
            "all_pages": True,
        }
    except Exception:
        return None, None


# search FEC API in parallel for retirees
def step_fec_api(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> int:
    dropped = drop_joint_source_entries(prev_cache)
    if dropped:
        prev_cache.save()
        logger.info(f"    FEC API: dropped {dropped:,} cached answer(s) read off a two-person filing")
    cleared = cleared_employers(df)
    unproven = drop_unproven_source_entries(prev_cache, df, cleared)
    if unproven:
        prev_cache.save()
        logger.info(f"    FEC API: dropped {unproven:,} cached answer(s) filed from a ZIP the donor never uses")

    fec_key = os.environ.get("FEC_API_KEY", "").strip()
    if not fec_key:
        logger.info("    FEC_API_KEY not found in .env - skipping")
        logger.info("    Get a free key at: https://api.open.fec.gov/developers/")
        return 0

    searches = _pending_fec_searches(df, prev_cache, donor_totals)
    for person in searches:
        person["cleared_employers"] = cleared.get(person["prev_key"].split(":", 1)[-1], set())
    if not searches:
        logger.info("    FEC API: 0 retired donors to search")
        return 0

    logger.info(f"    FEC API: {len(searches):,} retired donors to search")
    found = 0
    quota_spent = threading.Event()

    # skip once quota spent; next run retries
    def search(person):
        if quota_spent.is_set():
            return None, None
        return _fetch_fec_previous_employer(person, fec_key, requests.get)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(search, person) for person in searches]
        for done, future in enumerate(as_completed(futures), 1):
            cache_key, result = future.result()
            if result is RATE_LIMITED:
                if not quota_spent.is_set():
                    logger.info(f"    FEC API: hourly request limit reached after {done:,} donors; "
                                "the rest are searched on the next run")
                quota_spent.set()
                continue
            if cache_key and result:
                prev_cache.put(cache_key, result)
                if result.get("employer"):
                    found += 1
            if done % 50 == 0 and not quota_spent.is_set():
                prev_cache.save()
                logger.info(f"      ... {done}/{len(searches)} ({found:,} found)")

    prev_cache.save()
    logger.info(f"    FEC API: found {found:,} previous employers")
    return found
