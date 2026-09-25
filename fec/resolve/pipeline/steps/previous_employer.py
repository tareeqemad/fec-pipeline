"""Find a retired donor's previous employer from local records or the FEC API."""

import re

import pandas as pd

from fec.cleaning.employer_status import (
    classify_employer_statuses,
    is_real_employer,
)
from fec.cleaning.employer_synonyms import canonical_key
from fec.cleaning.previous_employer import (
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.log import get_logger
from fec.resolve.pipeline.constants import RETIRED
from fec.resolve.pipeline.helpers import _prev_key, _previous_employer_identity, _s

logger = get_logger(__name__)
PROTECTED_METHODS = {
    "manual_clear",
    "manual_override",
    "cleared_swapped_record",
}


# return all individuals and the latest row per retired donor
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


# apply the previous-employer contract to a single value
def _clean_employer(value) -> str:
    """Apply the same previous-employer contract to every source."""
    cleaned = normalize_previous_employer_value(value)
    # self-employment is known work history, not an empty answer
    return cleaned if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned) else ""


# true only when the filer explicitly reported self-employment
def _explicit_self_employment(value) -> bool:
    """True only when the filer explicitly reported self-employment."""
    upper = _s(value).strip().upper()
    compact = re.sub(r"[^A-Z]", "", upper)
    return (
        upper == "SELF"
        or upper.startswith(("SELF:", "SELF (", "SELF /"))
        or compact.startswith("SELFEMPLOYED")
    )


# build one clean cache entry, keeping raw spelling as provenance
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


# split retired donors into eligible and protected cache entries
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


# find candidate previous-employer rows from local records per donor
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


# update the previous-employer cache from cross-record candidates
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


# drop cached entries later evidence shows are wrong
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


# cache each retired donor's latest real employer from local records
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


