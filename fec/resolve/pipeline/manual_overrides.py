"""Load curated addresses, keeping uncertain research reviewable."""

import csv
from pathlib import Path

import pandas as pd

from fec.cleaning.manual_overrides import CLEAR_PREVIOUS_EMPLOYER
from fec.cleaning.previous_employer import normalize_previous_employer_value
from fec.config.cities import expand_city_abbreviations
from fec.log import get_logger

from .helpers import _prev_key, _s

logger = get_logger(__name__)

_REVIEW_MARKERS = ("MEDIUM", "LIKELY", "UNCERTAIN", "VERIFY")


def _manual_method(note: str) -> str:
    """Keep explicitly uncertain research in the review queue."""
    upper = note.upper()
    if "HIGH (VERIFIED)" not in upper and any(
        marker in upper for marker in _REVIEW_MARKERS
    ):
        return "manual_review"
    return "manual_override"


def _address_entry(row: dict) -> dict | None:
    address = (row.get("address") or "").strip()
    city = expand_city_abbreviations((row.get("city") or "").strip())
    state = (row.get("address_state") or row.get("state") or "").strip().upper()
    note = (row.get("note") or "").strip()
    suppressed = note.upper().startswith("INVALID:")
    if not address and not (city and state) and not suppressed:
        return None
    method = "manual_invalid" if suppressed else _manual_method(note)
    entry = {
        "employer_address": address,
        "employer_city": city,
        "employer_state": state,
        "employer_zip": (row.get("zip") or "").strip(),
        "method": method,
        "confidence": (
            "NONE" if suppressed else "LOW" if method == "manual_review" else "HIGH"
        ),
    }
    for field in ("source_name", "source_url"):
        value = (row.get(field) or "").strip()
        if value:
            entry[field] = value
    return entry


def _read_location_groups(csv_path: Path) -> dict:
    rows_by_name = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip().upper()
            entry = _address_entry(row)
            if not name or entry is None:
                continue
            group = rows_by_name.setdefault(name, {"primary": None, "extra": []})
            is_primary = (row.get("is_primary") or "true").strip().lower()
            if is_primary in {"true", "1", "yes"}:
                group["primary"] = entry
            else:
                group["extra"].append(entry)
    return rows_by_name


def _merge_manual_locations(existing: dict, manual: dict) -> dict:
    primary = manual["primary"]
    # INVALID says the company has no public office: no AI address of it stays
    invalid = primary is not None and primary.get("method") == "manual_invalid"
    ai_locations = [] if invalid else [
        location
        for location in existing.get("locations", [])
        if not str(location.get("method", "")).startswith("manual_")
    ]
    replacement = dict(existing)
    # a checked manual row (a correction or an INVALID) beats an AI web-search
    # answer; an uncertain one (manual_review) does not
    if primary is not None and (
        primary.get("method") in {"manual_override", "manual_invalid"}
        or not str(existing.get("method", "")).endswith("_search")
    ):
        replacement.update(primary)

    locations = ai_locations + manual["extra"]
    if locations:
        replacement["locations"] = locations
    else:
        replacement.pop("locations", None)
    return replacement


def _remove_deleted_locations(addr_cache, current_names: set[str]) -> int:
    entries = getattr(addr_cache, "data", addr_cache)
    removed = 0

    for name, existing in list(entries.items()):
        if name in current_names:
            continue

        method = str(existing.get("method", ""))
        if method.startswith("manual_"):
            addr_cache.discard(name)
            removed += 1
            continue

        locations = existing.get("locations", [])
        kept = [
            location
            for location in locations
            if not str(location.get("method", "")).startswith("manual_")
        ]
        if kept == locations:
            continue

        replacement = dict(existing)
        if kept:
            replacement["locations"] = kept
        else:
            replacement.pop("locations", None)
        addr_cache.put(name, replacement)
        removed += len(locations) - len(kept)

    return removed


def load_manual_locations(csv_path: Path, addr_cache) -> tuple[int, int]:
    """Load primary and additional employer locations into one cache entry."""
    if not csv_path.exists():
        logger.info(f"    manual locations: {csv_path.name} not found - skipping")
        return 0, 0

    rows_by_name = _read_location_groups(csv_path)
    removed = _remove_deleted_locations(addr_cache, set(rows_by_name))
    added = updated = 0
    for name, manual in rows_by_name.items():
        existing = dict(addr_cache.get(name) or {})
        replacement = _merge_manual_locations(existing, manual)
        if replacement == existing:
            continue
        if existing:
            updated += 1
        else:
            added += 1
        addr_cache.put(name, replacement)

    if added or updated or removed:
        addr_cache.save()
    logger.info(
        f"    manual locations: {added:,} added, {updated:,} updated "
        f"and {removed:,} removed from {csv_path.name}"
    )
    return added, updated


def _read_previous_employers(csv_path: Path, df: pd.DataFrame) -> dict:
    rows = df.assign(_sub_id=df["sub_id"].astype(str).str.strip()).set_index(
        "_sub_id",
        drop=False,
    )
    entries = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for override in csv.DictReader(handle):
            sub_id = (override.get("sub_id") or "").strip()
            raw_previous = (override.get("previous_employer") or "").strip()
            clear = raw_previous.upper() == CLEAR_PREVIOUS_EMPLOYER
            previous = "" if clear else normalize_previous_employer_value(raw_previous)
            if not sub_id or (not clear and not previous) or sub_id not in rows.index:
                continue
            source = rows.loc[sub_id]
            if isinstance(source, pd.DataFrame):
                source = source.iloc[0]
            donor_key = _s(source.get("donor_key")).strip()
            state = _s(source.get("contributor_state")).strip().upper()
            if not donor_key:
                continue
            entry = {
                "employer": previous,
                "state": state,
                "method": "manual_clear" if clear else "manual_override",
            }
            if previous:
                entry["employer_normalized"] = previous.upper()
            entries[_prev_key(donor_key)] = entry
    return entries


def _store_previous_employers(prev_cache, entries: dict) -> tuple[int, int]:
    added = 0
    updated = 0
    for key, entry in entries.items():
        existing = prev_cache.get(key)
        if existing == entry:
            continue
        if existing is None:
            added += 1
        else:
            updated += 1
        prev_cache.put(key, entry)
    return added, updated


def load_manual_previous_employers(
    csv_path: Path,
    df: pd.DataFrame,
    prev_cache,
) -> tuple[int, int]:
    """Protect curated work history from later cache discovery."""
    if not csv_path.exists() or "previous_employer" not in df.columns:
        return 0, 0

    entries = _read_previous_employers(csv_path, df)
    added, updated = _store_previous_employers(prev_cache, entries)
    if added or updated:
        prev_cache.save()
    logger.info(
        f"    manual previous employers: {added:,} added, {updated:,} updated "
        f"from {csv_path.name}"
    )
    return added, updated
