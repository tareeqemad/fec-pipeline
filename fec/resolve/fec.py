"""Search older FEC filings for a retired donor's previous employer."""
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from fec.cleaning.work.previous import normalize_previous_employer_value
from fec.cleaning.work.status import classify_employer_status
from fec.log import get_logger
from fec.resolve.cache import _cache_entry, _s
from fec.resolve.manual import cleared_employers
from fec.resolve.previous import (
    _donor_zips,
    _joint_name,
    _pending_fec_searches,
    _shared_zips,
    _zip5,
)

logger = get_logger(__name__)


FEC_BASE = "https://api.open.fec.gov/v1"


FEC_PAGE_SIZE = 100


FEC_MAX_PAGES = 20


RATE_LIMIT_WAIT_SECONDS = 5


RATE_LIMITED = object()


def _fec_filings(person: dict, fec_key: str, request_get,
                 quota_spent: threading.Event | None = None):
    """Every FEC filing under the donor's name and state, newest first, page by page.

    Yields RATE_LIMITED when the shared quota is spent, or None on other page
    errors: an incomplete search must not be recorded as 'not found'.
    """
    if quota_spent is None:
        quota_spent = threading.Event()
    params = {
        "api_key": fec_key,
        "contributor_name": person["name"],
        "contributor_state": person["state"],
        "per_page": FEC_PAGE_SIZE,
        "sort": "-contribution_receipt_date",
        "is_individual": "true",
    }
    for _page in range(FEC_MAX_PAGES):
        if quota_spent.is_set():
            yield RATE_LIMITED
            return
        response = request_get(f"{FEC_BASE}/schedules/schedule_a/", params=params, timeout=15)
        if response.status_code == 429:
            if quota_spent.wait(RATE_LIMIT_WAIT_SECONDS):
                yield RATE_LIMITED
                return
            response = request_get(f"{FEC_BASE}/schedules/schedule_a/", params=params, timeout=15)
            if response.status_code == 429:
                # Signal from this worker before it can take another queued donor.
                quota_spent.set()
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


def _fetch_fec_previous_employer(person: dict, fec_key: str, request_get,
                                quota_spent: threading.Event | None = None):
    """Fetch one donor's latest matching FEC filing and return its cache entry."""
    try:
        for record in _fec_filings(person, fec_key, request_get, quota_spent):
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
            if entry.get("employer") and entry["employer"].upper() not in person.get("cleared_employers", ()):
                return person["prev_key"], entry

        return person["prev_key"], {
            "employer": "",
            "method": "fec_api_not_found",
            "all_pages": True,
        }
    except Exception:
        return None, None


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
    cleared = cleared_employers(df)
    unproven = drop_unproven_source_entries(prev_cache, df, cleared)
    if unproven:
        prev_cache.save()
        logger.info(f"    FEC API: dropped {unproven:,} cached answer(s) with unproven donor locality or rejected employer values")

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
    quota_logged = False

    # skip the search once the quota is spent: the donor stays for the next run
    def search(person):
        if quota_spent.is_set():
            return None, None
        return _fetch_fec_previous_employer(person, fec_key, requests.get, quota_spent)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(search, person) for person in searches]
        for done, future in enumerate(as_completed(futures), 1):
            cache_key, result = future.result()
            if result is RATE_LIMITED:
                if not quota_logged:
                    logger.info(f"    FEC API: hourly request limit reached after {done:,} donors; "
                                "the rest are searched on the next run")
                    quota_logged = True
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


def _words(value) -> list[str]:
    """Return uppercase name/locality words without punctuation."""
    return re.findall(r"[A-Z0-9]+", _s(value).upper())


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
    # nor is a ZIP another donor of the same name also files from: the filing
    # could be either (RICHARD GOLDSTEIN, retiree and attorney, both in 33432)
    reported_zip = _zip5(record.get("contributor_zip"))
    return (bool(reported_zip) and reported_zip in _person_zips(person)
            and reported_zip not in (person.get("shared_zips") or ()))


def _person_zips(person: dict) -> set[str]:
    zips = {_zip5(value) for value in person.get("zips") or ()}
    zips.add(_zip5(person.get("zip")))
    return zips - {""}


def drop_unproven_source_entries(prev_cache, df: pd.DataFrame, cleared: dict | None = None) -> int:
    """Such an answer, or one from a ZIP another same-name donor files from, may be another person."""
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
        # an answer the current contract no longer reads as a company (ADVISOR,
        # COMPUTER ANALIST: a job title): searched again for a real employer
        elif not normalize_previous_employer_value(entry.get("employer", "")):
            unproven.append(key)
    for key in unproven:
        data.pop(key, None)
    return len(unproven)
