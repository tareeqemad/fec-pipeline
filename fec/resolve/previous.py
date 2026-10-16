"""Find retired donors' previous employers in the cleaned records."""
import re

import pandas as pd

from fec.cleaning.work.status import (
    classify_employer_statuses,
    is_real_employer,
)
from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.previous import (
    normalize_previous_employer_value,
)
from fec.log import get_logger
from fec.resolve.cache import RETIRED, _cache_entry, _prev_key, _previous_employer_identity, _s

logger = get_logger(__name__)


LOCAL_METHODS = frozenset({"cross_record", "cleaned_previous"})


PROTECTED_METHODS = {
    "manual_clear",
    "manual_override",
    "cleared_swapped_record",
}


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
    return {
        key: group for key, group in real_rows.groupby("donor_key") if key in eligible
    }


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
    eligible: set,
    rows_by_donor: dict,
    donor_to_cache_key: dict,
    prev_cache,
) -> int:
    cleared = 0
    for donor_key in eligible - rows_by_donor.keys():
        cache_key = donor_to_cache_key[donor_key]
        cached = prev_cache.get(cache_key)
        if not cached:
            continue
        # an entry read from the local file whose evidence is gone (a manual
        # override cleared the filing it came from) is not kept to come back
        if cached.get("method") in LOCAL_METHODS:
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
    rows_by_donor = _cross_record_candidates(individuals, eligible)
    found, refreshed, unchanged = _refresh_cross_record_cache(
        rows_by_donor,
        donor_to_cache_key,
        prev_cache,
    )
    cleared = _clear_invalid_cross_records(
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


_JOINT_GIVEN_RE = re.compile(r"\bAND\b|&|/|\+")


def _joint_name(value) -> bool:
    """Its work details may be either person's, so they prove nothing about one."""
    text = _s(value).upper()
    given = text.split(",", 1)[1] if "," in text else text
    return bool(_JOINT_GIVEN_RE.search(given))


def _zip5(value) -> str:
    digits = "".join(re.findall(r"\d", _s(value)))
    return digits[:5]


def _donor_zips(df: pd.DataFrame) -> dict[str, set[str]]:
    people = df[df["entity_type"] == "INDIVIDUAL"]
    zips = people["contributor_zip"].fillna("").astype(str).str.replace(r"\D", "", regex=True).str[:5]
    frame = pd.DataFrame({"key": people["donor_key"], "zip": zips})
    frame = frame[frame["zip"].str.len() == 5]
    return frame.groupby("key")["zip"].agg(set).to_dict()


def _shared_zips(df: pd.DataFrame) -> dict[str, set[str]]:
    if not {"contributor_name", "contributor_first_name", "contributor_last_name"} <= set(df.columns):
        return {}
    people = df[(df["entity_type"] == "INDIVIDUAL") & ~df["contributor_name"].map(_joint_name)]
    given = people["contributor_first_name"].fillna("").astype(str).str.upper().str.split()
    # a second whole given name ("STUART SARA") is a couple filing together, not a namesake
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


def _pending_fec_searches(
    df: pd.DataFrame,
    prev_cache,
    donor_totals: pd.DataFrame,
) -> list[dict]:
    """Return unresolved retired donors, largest donors first."""
    _, latest_retired = _retired_donors(df)
    tier_keys = set(donor_totals["donor_key"])
    donor_zips, shared_zips = _donor_zips(df), _shared_zips(df)
    donor_info = {
        row["donor_key"]: {
            "prev_key": _prev_key(row["donor_key"]),
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


def _fec_search_due(cached: dict | None) -> bool:
    """No answer yet, or a 'not found' from the old search that read only the first 100 filings."""
    if cached is None:
        return True
    return cached.get("method") == "fec_api_not_found" and not cached.get("all_pages")
