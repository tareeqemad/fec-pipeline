"""Cross-record step: previous employers from local filings."""

import pandas as pd

from fec.cleaning.employer_status import classify_employer_statuses
from fec.cleaning.employer_synonyms.canonical import canonical_key
from fec.log import get_logger
from fec.resolve.pipeline.previous_cache import (
    LOCAL_METHODS,
    PROTECTED_METHODS,
    cache_entry,
    clean_previous_employer,
    previous_cache_key,
    previous_employer_identity,
    retired_donors,
)

logger = get_logger(__name__)


# split retired donors into eligible and protected
def _eligible_retired_donors(latest_retired: pd.DataFrame, prev_cache):
    donor_to_cache_key = {
        row["donor_key"]: previous_cache_key(row["donor_key"])
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


# each donor's candidate rows from local filings
def _cross_record_candidates(individuals: pd.DataFrame, eligible: set):
    statuses = classify_employer_statuses(individuals)
    worked = statuses.isin(
        {
            "active",
            "self_employed",
        }
    )
    clean_employers = individuals["contributor_employer"].map(clean_previous_employer)
    work_rows = individuals.loc[worked & clean_employers.ne("")].assign(
        _candidate_employer=individuals["contributor_employer"],
        _candidate_method="cross_record",
        _candidate_priority=1,
    )
    previous = individuals["previous_employer"]
    clean_previous = previous.map(clean_previous_employer)
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
        entry = cache_entry(
            latest["_candidate_employer"],
            state=latest["contributor_state"],
            method=latest["_candidate_method"],
            source_date=latest["contribution_receipt_date"],
        )
        cached = prev_cache.get(cache_key)
        cached_name, _ = previous_employer_identity(cached)
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


# drop cached entries local evidence contradicts
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
    self_occupations = individuals["contributor_occupation"].map(clean_previous_employer)
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
        cached_name = clean_previous_employer(cached.get("employer"))
        invalid_cross_record = (
            cached.get("method") == "cross_record"
            and cached_name in invalid_by_donor.get(donor_key, set())
        )
        invalid_self_occupation = (
            cached.get("method") == "cleaned_previous"
            and cached_name in self_by_donor.get(donor_key, set())
        )
        # a local entry without evidence is dropped
        evidence_gone = cached.get("method") in LOCAL_METHODS
        if invalid_cross_record or invalid_self_occupation or evidence_gone:
            prev_cache.discard(cache_key)
            cleared += 1
    return cleared


# cache retirees' latest employer from local filings
def step_cross_record(df: pd.DataFrame, prev_cache) -> int:
    individuals, latest_retired = retired_donors(df)
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


