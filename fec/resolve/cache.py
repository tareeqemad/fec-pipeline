"""Read and build cached work-history entries for resolve."""

import re

import pandas as pd

from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.previous import (
    SELF_EMPLOYED_TAIL_RE,
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.cleaning.work.status import is_real_employer, single_letters
RETIRED = "RETIRED"
SELF_EMPLOYED = "SELF-EMPLOYED"
PREV_EMPLOYER_CACHE = "resolve_prev_employer.json"

def _s(val, default: str = "") -> str:
    if val is None or pd.isna(val):
        return default
    return str(val)

def _prev_key(donor_key, cycle=None) -> str:
    """Scope external history to the donor's election cycle."""
    key = f"donor:{_s(donor_key).strip()}"
    return f"{key}:{_s(cycle).strip()}" if _s(cycle).strip() else key

def _previous_for_row(prev_cache, row) -> dict | None:
    """Dated local history, else FEC evidence from this cycle."""
    donor, cycle = row.get("donor_key"), _s(row.get("two_year_transaction_period")).strip()
    filed = pd.to_datetime(row.get("contribution_receipt_date"), errors="coerce", utc=True)
    entry = prev_cache.get(_prev_key(donor))
    if entry and not str(entry.get("method", "")).startswith("fec_api"):
        source = pd.to_datetime(entry.get("source_date"), errors="coerce", utc=True)
        if entry.get("method") in {"manual_clear", "manual_override", "cleared_swapped_record"}:
            return entry
        if pd.isna(source) or pd.isna(filed) or source <= filed:
            return entry
    entry = prev_cache.get(_prev_key(donor, cycle)) if cycle else None
    if not entry or entry.get("method") != "fec_api" or _s(entry.get("source_cycle")) != cycle:
        return None
    source = pd.to_datetime(entry.get("source_date"), errors="coerce", utc=True)
    if pd.notna(source) and pd.notna(filed) and source <= filed:
        return entry
    return None

def _previous_employer_identity(entry: dict | None) -> tuple[str, tuple[str, ...]]:
    """Return the clean employer name and compatible old cache keys."""
    if not entry or entry.get("method") == "fec_api_unverified":
        return "", ()

    raw_name = _s(entry.get("employer")).strip()
    normalized_name = _s(entry.get("employer_normalized")).strip()
    source_name = _s(entry.get("employer_source")).strip()

    candidates = (raw_name, normalized_name, source_name)
    if source_name and entry.get("method") in {"fec_api", "cross_record", "cleaned_previous"}:
        filed = normalize_previous_employer_value(source_name)
        if filed == SELF_EMPLOYED or is_real_employer(filed):
            candidates = (source_name,)
    clean_name = ""
    for candidate in candidates:
        cleaned = normalize_previous_employer_value(candidate)
        if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned):
            clean_name = cleaned
            break
    if not clean_name:
        return "", ()

    keys = []
    for name in (clean_name, normalized_name, raw_name, source_name):
        key = name.strip().upper()
        if (key and key not in keys
                and canonical_key(normalize_previous_employer_value(key)) == canonical_key(clean_name)):
            keys.append(key)
    return clean_name, tuple(keys)

def _explicit_self_employment(value) -> bool:
    upper = _s(value).strip().upper()
    compact = single_letters(re.sub(r"[^A-Z]", "", upper))
    return (
        upper == "SELF"
        or upper.startswith(("SELF:", "SELF (", "SELF /"))
        or compact.startswith("SELFEMPLOY")
        or bool(SELF_EMPLOYED_TAIL_RE.search(upper))
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
    source_street_1="",
    source_street_2="",
    source_sub_id="",
    source_cycle="", source_committee_id="",
) -> dict:
    """One clean cache entry, keeping raw spelling as provenance."""
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
        "source_street_1": source_street_1,
        "source_street_2": source_street_2,
        "source_sub_id": source_sub_id,
        "source_cycle": source_cycle, "source_committee_id": source_committee_id,
    }
    entry.update({key: _s(value).strip() for key, value in provenance.items() if value})
    return entry
