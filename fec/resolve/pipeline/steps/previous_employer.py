"""Find a retired donor's previous employer from local records or the FEC API."""

import os
import re
import time

import pandas as pd

from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.previous_employer import (
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.cleaning.employer_status import (
    classify_employer_status,
    classify_employer_statuses,
    is_real_employer,
)
from fec.log import get_logger

from ..constants import RETIRED
from ..helpers import (
    _prev_key,
    _previous_employer_identity,
    _s,
)

logger = get_logger(__name__)
FEC_BASE = "https://api.open.fec.gov/v1"
FEC_PAGE_SIZE = 100
# 2,000 filings under one name and state: past that the search is given up as not found
FEC_MAX_PAGES = 20
PROTECTED_METHODS = {
    "manual_clear",
    "manual_override",
    "cleared_swapped_record",
}


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


def _zip5(value) -> str:
    digits = "".join(re.findall(r"\d", _s(value)))
    return digits[:5]


def _same_fec_donor(person: dict, record: dict) -> bool:
    """Require matching identity and locality before trusting an FEC record."""
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

    expected_zip = _zip5(person.get("zip"))
    reported_zip = _zip5(record.get("contributor_zip"))
    if expected_zip and expected_zip == reported_zip:
        return True

    expected_city = _words(person.get("city"))
    reported_city = _words(record.get("contributor_city"))
    return bool(expected_city and expected_city == reported_city)


def _retired_donors(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return all individuals and the latest row for every retired donor."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    retired_keys = set(
        individuals.loc[
            individuals["contributor_employer"] == RETIRED, "donor_key"
        ].unique()
    )
    latest = (
        individuals[individuals["donor_key"].isin(retired_keys)]
        .sort_values("contribution_receipt_date", ascending=False)
        .drop_duplicates("donor_key", keep="first")
    )
    return individuals, latest


def _clean_employer(value) -> str:
    """Apply the same previous-employer contract to every source."""
    cleaned = normalize_previous_employer_value(value)
    # self-employment is known work history, not an empty answer
    return cleaned if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned) else ""


def _explicit_self_employment(value) -> bool:
    """True only when the filer explicitly reported self-employment."""
    upper = _s(value).strip().upper()
    compact = re.sub(r"[^A-Z]", "", upper)
    return (
        upper == "SELF"
        or upper.startswith(("SELF:", "SELF (", "SELF /"))
        or compact.startswith("SELFEMPLOYED")
    )


def _cache_entry(
    raw_employer,
    *,
    state,
    method,
    source_date="",
    source_name="",
    source_city="",
    source_zip="",
) -> dict:
    """Build one clean cache entry while retaining raw spelling as provenance."""
    raw_name = _s(raw_employer).strip()
    employer = normalize_previous_employer_value(raw_name)
    if employer == "SELF-EMPLOYED" and not _explicit_self_employment(raw_name):
        employer = ""
    if source_name:
        employer = preserve_own_named_legal_employer(
            employer,
            raw_name,
            source_name,
        )
    if not employer:
        return {"employer": "", "method": f"{method}_not_found"}

    entry = {
        "employer": employer,
        "employer_normalized": employer.upper(),
        "state": _s(state).strip(),
        "method": method,
    }
    if raw_name.upper() != employer.upper():
        entry["employer_source"] = raw_name
    provenance = {
        "source_date": source_date,
        "source_name": source_name,
        "source_city": source_city,
        "source_zip": source_zip,
    }
    entry.update({key: _s(value).strip() for key, value in provenance.items() if value})
    return entry


def _eligible_retired_donors(latest_retired: pd.DataFrame, prev_cache):
    donor_to_cache_key = {
        row["donor_key"]: _prev_key(row["donor_key"])
        for _, row in latest_retired.iterrows()
    }

    eligible = set()
    protected = 0
    for donor_key, cache_key in donor_to_cache_key.items():
        cached = prev_cache.get(cache_key)
        if cached and cached.get("method") in PROTECTED_METHODS:
            protected += 1
        else:
            eligible.add(donor_key)
    return donor_to_cache_key, eligible, protected


def _cross_record_candidates(individuals: pd.DataFrame, eligible: set):
    statuses = classify_employer_statuses(individuals)
    worked = statuses.isin(
        {
            "active",
            "self_employed",
        }
    )
    clean_employers = individuals["contributor_employer"].map(_clean_employer)
    work_rows = individuals.loc[worked & clean_employers.ne("")].assign(
        _candidate_employer=individuals["contributor_employer"],
        _candidate_method="cross_record",
        _candidate_priority=1,
    )
    previous = individuals.get(
        "previous_employer",
        pd.Series("", index=individuals.index),
    )
    clean_previous = previous.map(_clean_employer)
    previous_rows = individuals.loc[clean_previous.ne("")].assign(
        _candidate_employer=previous,
        _candidate_method="cleaned_previous",
        _candidate_priority=0,
    )
    real_rows = pd.concat([previous_rows, work_rows]).sort_values(
        ["_candidate_priority", "contribution_receipt_date"],
        ascending=[True, False],
    )
    rows_by_donor = {
        key: group for key, group in real_rows.groupby("donor_key") if key in eligible
    }
    return rows_by_donor, clean_employers, statuses


def _refresh_cross_record_cache(
    rows_by_donor: dict,
    donor_to_cache_key: dict,
    prev_cache,
) -> tuple[int, int, int]:
    found = refreshed = unchanged = 0
    for donor_key, group in rows_by_donor.items():
        cache_key = donor_to_cache_key[donor_key]
        latest = group.iloc[0]
        entry = _cache_entry(
            latest["_candidate_employer"],
            state=latest.get("contributor_state", ""),
            method=latest["_candidate_method"],
            source_date=latest.get("contribution_receipt_date", ""),
        )
        cached = prev_cache.get(cache_key)
        cached_name, _ = _previous_employer_identity(cached)
        if cached_name and canonical_key(cached_name) == canonical_key(
            entry.get("employer", "")
        ):
            unchanged += 1
            continue
        if entry == cached:
            unchanged += 1
            continue
        prev_cache.put(cache_key, entry)
        if cached is None:
            found += 1
        else:
            refreshed += 1
    return found, refreshed, unchanged


def _clear_invalid_cross_records(
    individuals: pd.DataFrame,
    statuses,
    clean_employers,
    eligible: set,
    rows_by_donor: dict,
    donor_to_cache_key: dict,
    prev_cache,
) -> int:
    invalid_employers = individuals.loc[
        statuses.eq("not_employed") & clean_employers.ne("")
    ].assign(_invalid_name=clean_employers)
    self_occupations = individuals["contributor_occupation"].map(_clean_employer)
    invalid_occupations = individuals.loc[
        statuses.eq("self_employed") & self_occupations.ne("")
    ].assign(_invalid_name=self_occupations)

    invalid_by_donor = {
        donor_key: set(group["_invalid_name"])
        for donor_key, group in invalid_employers.groupby("donor_key")
    }
    self_by_donor = {
        donor_key: set(group["_invalid_name"])
        for donor_key, group in invalid_occupations.groupby("donor_key")
    }

    cleared = 0
    for donor_key in eligible - rows_by_donor.keys():
        cache_key = donor_to_cache_key[donor_key]
        cached = prev_cache.get(cache_key)
        if not cached:
            continue
        cached_name = _clean_employer(cached.get("employer"))
        invalid_cross_record = (
            cached.get("method") == "cross_record"
            and cached_name in invalid_by_donor.get(donor_key, set())
        )
        invalid_self_occupation = (
            cached.get("method") == "cleaned_previous"
            and cached_name in self_by_donor.get(donor_key, set())
        )
        if invalid_cross_record or invalid_self_occupation:
            prev_cache.discard(cache_key)
            cleared += 1
    return cleared


def step_cross_record(df: pd.DataFrame, prev_cache) -> int:
    """Cache each retired donor's latest real employer from the loaded CSV."""
    individuals, latest_retired = _retired_donors(df)
    donor_to_cache_key, eligible, protected = _eligible_retired_donors(
        latest_retired,
        prev_cache,
    )
    rows_by_donor, clean_employers, statuses = _cross_record_candidates(
        individuals,
        eligible,
    )
    found, refreshed, unchanged = _refresh_cross_record_cache(
        rows_by_donor,
        donor_to_cache_key,
        prev_cache,
    )
    cleared = _clear_invalid_cross_records(
        individuals,
        statuses,
        clean_employers,
        eligible,
        rows_by_donor,
        donor_to_cache_key,
        prev_cache,
    )

    changed = found + refreshed + cleared
    if changed:
        prev_cache.save()
    no_local = len(eligible) - len(rows_by_donor)
    logger.info(
        f"    Cross-record: {found:,} new, {refreshed:,} refreshed, "
        f"{unchanged:,} current, {cleared:,} cleared, "
        f"{no_local:,} no local employer, "
        f"{protected:,} protected"
    )
    return changed


def _pending_fec_searches(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> list[dict]:
    """Return unresolved retired donors, largest donors first."""
    _, latest_retired = _retired_donors(df)
    tier_keys = set(donor_totals["donor_key"])
    donor_info = {
        row["donor_key"]: {
            "prev_key": _prev_key(row["donor_key"]),
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
            "city": row.get("contributor_city", ""),
            "zip": row.get("contributor_zip", ""),
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


def _fec_search_due(cached: dict | None) -> bool:
    """No answer yet, or a 'not found' from the old search that read only the first 100 filings."""
    if cached is None:
        return True
    return cached.get("method") == "fec_api_not_found" and not cached.get("all_pages")


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
            time.sleep(5)
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


def _fetch_fec_previous_employer(person: dict, fec_key: str, request_get):
    """Fetch one donor's latest matching FEC filing and return its cache entry."""
    try:
        for record in _fec_filings(person, fec_key, request_get):
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


def step_fec_api(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> int:
    """Find remaining retired donors' latest real employer through the FEC API."""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

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
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [
            pool.submit(_fetch_fec_previous_employer, person, fec_key, requests.get)
            for person in searches
        ]
        for done, future in enumerate(as_completed(futures), 1):
            cache_key, result = future.result()
            if cache_key and result:
                prev_cache.put(cache_key, result)
                if result.get("employer"):
                    found += 1
            if done % 50 == 0:
                prev_cache.save()
                logger.info(f"      ... {done}/{len(searches)} ({found:,} found)")

    prev_cache.save()
    logger.info(f"    FEC API: found {found:,} previous employers")
    return found
