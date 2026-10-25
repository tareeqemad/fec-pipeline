"""Find retired donors' previous employers in the cleaned records."""
import hashlib
import json
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
from fec.env import RAW_CSV
from fec.committees import committee_id_to_name
from fec.donor_match.normalize import NICKNAME_MAP
from fec.donor_match.rules import KEY_MERGES, resolve_donor_key
from fec.resolve.cache import RETIRED, _cache_entry, _prev_key, _previous_employer_identity, _previous_for_row, _s

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
    retired_keys = set(individuals.loc[individuals["contributor_employer"].eq(RETIRED), "donor_key"])
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
    donor_to_cache_key = {key: _prev_key(key) for key in latest_retired["donor_key"]}

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
    worked = statuses.isin({"active", "self_employed"})
    clean_employers = individuals["contributor_employer"].map(_clean_employer)
    work_rows = individuals.loc[worked & clean_employers.ne("")].assign(
        _candidate_employer=individuals["contributor_employer"],
        _candidate_method="cross_record",
        _candidate_priority=1,
    )
    previous = individuals.get("previous_employer", pd.Series("", index=individuals.index))
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
    return {key: group for key, group in real_rows.groupby("donor_key") if key in eligible}

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
        if cached_name and cached.get("method") != "fec_api_unverified" and canonical_key(cached_name) == canonical_key(
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
    donor_to_cache_key, eligible, protected = _eligible_retired_donors(latest_retired, prev_cache)
    rows_by_donor = _cross_record_candidates(individuals, eligible)
    found, refreshed, unchanged = _refresh_cross_record_cache(rows_by_donor, donor_to_cache_key, prev_cache)
    cleared = _clear_invalid_cross_records(eligible, rows_by_donor, donor_to_cache_key, prev_cache)

    changed = found + refreshed + cleared
    if changed:
        prev_cache.save()
    no_local = len(eligible) - len(rows_by_donor)
    logger.info(
        "Work history: %s cache changes | %s kept | %s without a local employer",
        f"{changed:,}", f"{unchanged + protected:,}", f"{no_local:,}",
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

def _shared_zips(df: pd.DataFrame) -> dict[str, set[str]]:
    if not {"contributor_name", "contributor_first_name", "contributor_last_name"} <= set(df.columns):
        return {}
    people = df[(df["entity_type"] == "INDIVIDUAL") & ~df["contributor_name"].map(_joint_name)]
    given = people["contributor_first_name"].fillna("").astype(str).str.upper().str.split()
    # a second whole given name ("STUART SARA") is a couple filing together, not a namesake
    people = people[given.map(lambda words: all(len(w.strip(".")) <= 1 for w in words[1:]))]
    first = given[people.index].str[0].fillna("").map(lambda name: NICKNAME_MAP.get(name, name))
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
    """Search each unresolved donor and election cycle once."""
    retired = df[df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].eq(RETIRED)]
    latest = retired.sort_values("contribution_receipt_date", ascending=False).drop_duplicates(
        ["donor_key", "two_year_transaction_period"])
    totals = donor_totals.set_index("donor_key")["donor_total"].to_dict()
    evidence = _donor_evidence(df)
    searches = []
    for row in latest.to_dict("records"):
        key, cycle = row["donor_key"], _s(row.get("two_year_transaction_period")).strip()
        if key not in totals or not cycle.isdigit() or int(cycle) % 2:
            continue
        cycle = int(cycle)
        cached = _previous_for_row(prev_cache, row)
        if cached and (cached.get("method") in PROTECTED_METHODS or _previous_employer_identity(cached)[0]):
            continue
        info = evidence.get((key, cycle), {})
        if not info.get("filings") or not info.get("latest_date") or not info.get("committee_ids"):
            continue
        names = {row["contributor_name"], *(filing["contributor_name"] for filing in info["filings"]
                 if filing["contributor_name"] and not _joint_name(filing["contributor_name"]))}
        for name in tuple(names):
            last, comma, given = name.partition(",")
            first, _, rest = given.strip().partition(" ")
            if comma:
                names.add(f"{last}, {NICKNAME_MAP.get(first, first)} {rest}".strip())
            names.update(f"{last}, {alias} {rest}".strip() for alias, canonical in NICKNAME_MAP.items()
                         if comma and canonical == NICKNAME_MAP.get(first, first))
        terms = {
            "names": sorted(names),
            "states": sorted({filing["contributor_state"] for filing in info["filings"] if filing["contributor_state"]}),
            "evidence": hashlib.sha256(json.dumps([
                sorted({tuple(sorted(filing.items())) for filing in info["filings"]}),
                sorted(info.get("shared_zips", ())), sorted(info["committee_ids"]),
            ]).encode()).hexdigest(),
            "matcher": 2,
        }
        cache_key = _prev_key(key, cycle)
        scoped = prev_cache.get(cache_key)
        if not _fec_search_due(scoped, row["contribution_receipt_date"], terms) and (scoped or {}).get("method") != "fec_api":
            continue
        searches.append({
            "prev_key": cache_key, "cycle": cycle,
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
            "city": row.get("contributor_city", ""),
            "zip": row.get("contributor_zip", ""),
            **info, "search_terms": terms, "total": totals[key],
            "latest_date": _s(row["contribution_receipt_date"])[:10],
        })
    searches.sort(key=lambda entry: -entry["total"])
    return searches

def _fec_search_due(cached: dict | None, latest_date=None, search_terms=None) -> bool:
    """Retry a miss when its date, matching rules or filed evidence changes."""
    if cached is None:
        return True
    return (cached.get("method") == "fec_api_unverified"
            or cached.get("method") == "fec_api_not_found" and (
                search_terms is not None and cached.get("search_terms") != search_terms
                or not cached.get("all_pages") or latest_date is not None
                and _s(cached.get("searched_through"))[:10] < _s(latest_date)[:10]))

def _donor_evidence(df: pd.DataFrame) -> dict:
    """Use raw addresses; reviewed identities also keep their earlier addresses."""
    fields = ["contributor_name", "contributor_street_1", "contributor_street_2",
              "contributor_state", "contributor_zip"]
    people = df[df["entity_type"].eq("INDIVIDUAL")]
    period = "two_year_transaction_period"
    if period not in people:
        return {}
    ids = {name: key for key, name in committee_id_to_name().items()}
    frame = people.reindex(columns=["donor_key", "sub_id", "contribution_receipt_date", period, "recipient_committee"],
                           fill_value="").fillna("")
    filed = {}
    if RAW_CSV.exists() and frame["sub_id"].ne("").any():
        raw = pd.read_csv(RAW_CSV, usecols=["sub_id", period, *fields], dtype=str, keep_default_na=False)
        filed = raw.drop_duplicates("sub_id").set_index("sub_id").to_dict("index")
    evidence, seen, history = {}, {}, {}
    reviewed = {resolve_donor_key(key) for key in KEY_MERGES.values()}
    for row in frame.to_dict("records"):
        cycle = _s(row[period]).strip()
        if not cycle.isdigit() or int(cycle) % 2:
            continue
        key = row["donor_key"], int(cycle)
        item = evidence.setdefault(key, {"filings": [], "latest_date": "", "committee_ids": set()})
        item["latest_date"] = max(item["latest_date"], str(row["contribution_receipt_date"])[:10])
        if committee := ids.get(row["recipient_committee"]):
            item["committee_ids"].add(committee)
        source = filed.get(str(row["sub_id"]))
        if source is None or _s(source.get(period)).strip() != cycle:
            continue
        values = tuple(_s(source.get(field)).strip() for field in fields)
        if row["donor_key"] in reviewed and row["contribution_receipt_date"]:
            history.setdefault(row["donor_key"], []).append((str(row["contribution_receipt_date"])[:10], values))
        known = seen.setdefault(key, set())
        if values not in known:
            item["filings"].append(dict(zip(fields, values)))
            known.add(values)
    shared = _shared_zips(people)
    for (key, cycle), item in evidence.items():
        known = seen.setdefault((key, cycle), set())
        for date, values in history.get(key, ()):
            if date <= item["latest_date"] and values not in known:
                item["filings"].append(dict(zip(fields, values)))
                known.add(values)
        item["shared_zips"] = shared.get(key, set())
    return evidence
