"""Find a retired donor's previous employer in their older FEC filings."""
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
from fec.log import get_logger
from fec.resolve.pipeline.helpers import (
    _prev_key,
    _s,
)
from fec.resolve.pipeline.steps.previous_employer import _cache_entry, _retired_donors

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"


FEC_PAGE_SIZE = 100


# 2,000 filings under one name and state: past that the search is given up as not found
FEC_MAX_PAGES = 20


# a 429 is retried once after this wait; a second 429 means the hourly quota is spent
RATE_LIMIT_WAIT_SECONDS = 5


# the FEC answered 429 twice: the key's hourly quota is used up
RATE_LIMITED = object()


# split a value into uppercase alphanumeric words
def _words(value) -> list[str]:
    """Return uppercase name/locality words without punctuation."""
    return re.findall(r"[A-Z0-9]+", _s(value).upper())


# split a name into last, first and middle-initial parts
def _name_parts(value) -> tuple[str, str, str]:
    """Return (last, first, middle initial) from an FEC-style name."""
    text = _s(value).strip()
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


# "SANDBERG, ADELE AND JOEL", "COHEN, ANN & BOB": one filing naming two people
_JOINT_GIVEN_RE = re.compile(r"\bAND\b|&|/|\+")


# true for a filing name that carries two people
def _joint_name(value) -> bool:
    """Its work details may be either person's, so they prove nothing about one."""
    text = _s(value).upper()
    given = text.split(",", 1)[1] if "," in text else text
    return bool(_JOINT_GIVEN_RE.search(given))


# drop cached FEC answers taken from a two-person filing
def drop_joint_source_entries(prev_cache) -> int:
    """A previous employer read off a joint filing is the household's, not the donor's."""
    data = getattr(prev_cache, "data", prev_cache)
    joint = [
        key for key, entry in data.items()
        if isinstance(entry, dict) and str(entry.get("method", "")).startswith("fec_api")
        and _joint_name(entry.get("source_name"))
    ]
    for key in joint:
        data.pop(key, None)
    return len(joint)


# extract the first 5 digits of a ZIP code
def _zip5(value) -> str:
    digits = "".join(re.findall(r"\d", _s(value)))
    return digits[:5]


# check name, locality and ZIP/city match before trusting a record
def _same_fec_donor(person: dict, record: dict) -> bool:
    """Require matching identity and locality before trusting an FEC record."""
    if _joint_name(record.get("contributor_name")) and not _joint_name(person.get("name")):
        return False
    expected = _name_parts(person.get("name"))
    reported = _name_parts(record.get("contributor_name"))
    if not all(expected[:2]) or expected[:2] != reported[:2]:
        return False
    if expected[2] and reported[2] and expected[2] != reported[2]:
        return False

    expected_state = _s(person.get("state")).strip().upper()
    reported_state = _s(record.get("contributor_state")).strip().upper()
    if expected_state and reported_state and expected_state != reported_state:
        return False

    # the filing must come from a ZIP the donor files from: a name and a city
    # are not proof (two RICHARD GOLDSTEINs file from Boca Raton 33431/33432)
    reported_zip = _zip5(record.get("contributor_zip"))
    return bool(reported_zip) and reported_zip in _person_zips(person)


# the ZIP5s a donor files from: all of them when known, else the latest
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


# drop cached FEC answers read off a filing from a ZIP the donor never files from
def drop_unproven_source_entries(prev_cache, df: pd.DataFrame) -> int:
    """Such an answer may be another person with the same name in the same city."""
    data = getattr(prev_cache, "data", prev_cache)
    donor_zips = _donor_zips(df)
    unproven = []
    for key, entry in data.items():
        if not (isinstance(entry, dict) and entry.get("method") == "fec_api"):
            continue
        source_zip = _zip5(entry.get("source_zip"))
        zips = donor_zips.get(key.split(":", 1)[-1])
        if source_zip and zips and source_zip not in zips:
            unproven.append(key)
    for key in unproven:
        data.pop(key, None)
    return len(unproven)


# list unresolved retired donors to search, largest first
def _pending_fec_searches(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> list[dict]:
    """Return unresolved retired donors, largest donors first."""
    _, latest_retired = _retired_donors(df)
    tier_keys = set(donor_totals["donor_key"])
    donor_zips = _donor_zips(df)
    donor_info = {
        row["donor_key"]: {
            "prev_key": _prev_key(row["donor_key"]),
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
            "city": row.get("contributor_city", ""),
            "zip": row.get("contributor_zip", ""),
            "zips": donor_zips.get(row["donor_key"], set()),
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


# decide if a cached result needs a fuller FEC search
def _fec_search_due(cached: dict | None) -> bool:
    """No answer yet, or a 'not found' from the old search that read only the first 100 filings."""
    if cached is None:
        return True
    return cached.get("method") == "fec_api_not_found" and not cached.get("all_pages")


# page through all FEC filings for a donor's identity
def _fec_filings(person: dict, fec_key: str, request_get):
    """Every FEC filing under the donor's name and state, newest first, page by page.

    Yields None when a page cannot be read (rate limit, error): the search is
    then incomplete and must not be recorded as 'not found'.
    """
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
    """Fetch one donor's latest matching FEC filing and return its cache entry."""
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
            entry = _cache_entry(
                record.get("contributor_employer", ""),
                state=record.get("contributor_state", person["state"]),
                method="fec_api",
                source_date=record.get("contribution_receipt_date", ""),
                source_name=record.get("contributor_name", ""),
                source_city=record.get("contributor_city", ""),
                source_zip=record.get("contributor_zip", ""),
            )
            if entry.get("employer"):
                return person["prev_key"], entry

        return person["prev_key"], {
            "employer": "",
            "method": "fec_api_not_found",
            "all_pages": True,
        }
    except Exception:
        return None, None


# search the FEC API in parallel for retired donors
def step_fec_api(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> int:
    """Find remaining retired donors' latest real employer through the FEC API."""
    dropped = drop_joint_source_entries(prev_cache)
    if dropped:
        prev_cache.save()
        logger.info(f"    FEC API: dropped {dropped:,} cached answer(s) read off a two-person filing")
    unproven = drop_unproven_source_entries(prev_cache, df)
    if unproven:
        prev_cache.save()
        logger.info(f"    FEC API: dropped {unproven:,} cached answer(s) filed from a ZIP the donor never uses")

    fec_key = os.environ.get("FEC_API_KEY", "").strip()
    if not fec_key:
        logger.info("    FEC_API_KEY not found in .env - skipping")
        logger.info("    Get a free key at: https://api.open.fec.gov/developers/")
        return 0

    searches = _pending_fec_searches(df, prev_cache, donor_totals)
    if not searches:
        logger.info("    FEC API: 0 retired donors to search")
        return 0

    logger.info(f"    FEC API: {len(searches):,} retired donors to search")
    found = 0
    quota_spent = threading.Event()

    # skip the search once the quota is spent: the donor stays for the next run
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
