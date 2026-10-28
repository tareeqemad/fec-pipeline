"""Store resolve results, preserve lookup keys, and combine employer aliases."""

import json
import os
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.previous import (
    SELF_EMPLOYED_TAIL_RE,
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.cleaning.work.status import is_real_employer, single_letters
from fec.io import write_json_atomic
from fec.log import get_logger
RETIRED = "RETIRED"
SELF_EMPLOYED = "SELF-EMPLOYED"
EMPLOYER_ADDR_CACHE = "resolve_employer_addr.json"
PREV_EMPLOYER_CACHE = "resolve_prev_employer.json"

class Cache:
    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                self.data = json.load(handle)

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def discard(self, key):
        self.data.pop(key, None)

    def save(self):
        write_json_atomic(self.path, self.data, ensure_ascii=False)

    def __len__(self):
        return len(self.data)

def _read_json(path) -> dict:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

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

def _same_entity(a: str, b: str) -> bool:
    """Match only names joined by the employer rules."""
    key_a, key_b = canonical_key(a), canonical_key(b)
    return bool(key_a) and key_a == key_b

def verified_address_entries(entries: dict) -> dict:
    """Ignore unsupported old aliases without deleting saved cache entries."""
    kept = {name: entry for name, entry in entries.items() if isinstance(entry, dict)
            and (not entry.get("alias_of") or _same_entity(name, entry["alias_of"])
                 or entry.get("method") == "manual_invalid")}
    if len(kept) != len(entries):
        get_logger(__name__).warning(
            "%s unsupported employer cache aliases ignored; saved entries kept",
            len(entries) - len(kept),
        )
    return kept

def _address_key(entry: dict) -> str:
    fields = (
        entry.get('employer_address'),
        entry.get('employer_city'),
        entry.get('employer_state'),
        entry.get('employer_zip'),
    )
    if not fields[0]:
        return ''
    return '|'.join(
        re.sub(r'[^A-Z0-9]+', ' ', str(value or '').upper()).strip()
        for value in fields
    )

def _score(entry: dict) -> tuple:
    """Rank the address, confidence, then source method."""
    has_address = 1 if entry.get('employer_address') else 0
    confidence_rank = {'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(entry.get('confidence', ''), 0)
    method = entry.get('method', '')
    # Search results rank above other AI answers; misses stay generic.
    if method.startswith('ai_') and method.endswith('_search'):
        method_priority = 3
    elif method.startswith('ai_') and not method.endswith('_not_found'):
        method_priority = 2
    elif method:
        method_priority = 1
    else:
        method_priority = 0
    return (has_address, confidence_rank, method_priority)

def _entries_by_address(data: dict) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, entry in data.items():
        if not isinstance(entry, dict):
            continue
        if entry.get('alias_of'):
            continue
        if entry.get('method') == 'manual_override':
            continue
        if entry.get('confidence') not in ('HIGH', 'MEDIUM'):
            continue
        address = _address_key(entry)
        if address:
            grouped[address].append(name)
    return grouped

def _cluster_company_names(names: list[str]) -> list[list[str]]:
    """Cluster same-company spellings while preserving cache order."""
    clusters: dict[str | int, list[str]] = {}
    for index, name in enumerate(names):
        key = canonical_key(name)
        # Each name without a key stays separate.
        group_key = key if key else index
        clusters.setdefault(group_key, []).append(name)
    return list(clusters.values())

def _canonical_name(cluster: list[str]) -> str:
    """Prefer the cached reviewed name, else a stable spelling."""
    reviewed = {reviewed_employer_name(name) for name in cluster} & set(cluster)
    return min(reviewed or cluster)

def _alias_mapping(grouped: dict[str, list[str]]) -> tuple[dict[str, str], int]:
    mapping: dict[str, str] = {}
    groups = 0
    for names in grouped.values():
        if len(names) < 2:
            continue
        for cluster in _cluster_company_names(names):
            if len(cluster) < 2:
                continue
            groups += 1
            canonical = _canonical_name(cluster)
            mapping.update(
                {name: canonical for name in cluster if name != canonical}
            )
    return mapping, groups

def _settle_canonical_entries(data: dict, mapping: dict[str, str]) -> None:
    """Give each canonical name the strongest entry among its aliases."""
    for variant, canonical in mapping.items():
        if _score(data[variant]) > _score(data[canonical]):
            data[canonical] = data[variant]

def _write_aliases(data: dict, mapping: dict[str, str]) -> None:
    """Keep every spelling resolvable without triggering another lookup."""
    for variant, canonical in mapping.items():
        canonical_entry = data[canonical]
        alias = {
            key: value
            for key, value in canonical_entry.items()
            if key != 'alias_of'
        }
        alias['alias_of'] = canonical
        data[variant] = alias

def dedup_by_resolved_address(addr_cache) -> tuple[int, int]:
    """Alias same-company entries resolved to the same complete address."""
    mapping, groups = _alias_mapping(_entries_by_address(addr_cache.data))

    if not mapping:
        return 0, 0

    _settle_canonical_entries(addr_cache.data, mapping)
    _write_aliases(addr_cache.data, mapping)
    addr_cache.save()
    return len(mapping), groups

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
