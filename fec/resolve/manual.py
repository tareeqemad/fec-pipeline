"""Load reviewed previous employers and display names."""

import csv
from pathlib import Path

import pandas as pd

from fec.cleaning.rules.manual import CLEAR_PREVIOUS_EMPLOYER, _read_overrides
from fec.cleaning.work.previous import normalize_previous_employer_value
from fec.env import MANUAL_EMPLOYER_OVERRIDES_CSV, RAW_CSV
from fec.log import get_logger
from fec.resolve.cache import _prev_key, _s

logger = get_logger(__name__)

def previous_employer_display_names() -> dict[str, str]:
    """Read filing-specific names without treating them as work history."""
    if not MANUAL_EMPLOYER_OVERRIDES_CSV.exists():
        return {}
    display_ids = set()
    overrides = _read_overrides(False, display_previous_ids=display_ids)
    return {sid: overrides[sid]["previous_employer"] for sid in display_ids}


def _read_previous_employers(csv_path: Path, df: pd.DataFrame) -> dict:
    rows = df.assign(_sub_id=df["sub_id"].astype(str).str.strip()).set_index(
        "_sub_id",
        drop=False,
    )
    entries = {}
    with csv_path.open(encoding="utf-8", newline="") as handle:
        for override in csv.DictReader(handle):
            # Display corrections do not establish new work history.
            if (override.get("after_matching") or "").strip().lower() == "true":
                continue
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
    if not csv_path.exists():
        return 0, 0

    entries = _read_previous_employers(csv_path, df)
    added, updated = _store_previous_employers(prev_cache, entries)
    if added or updated:
        prev_cache.save()
        logger.info("Reviewed work history: %s added, %s updated", f"{added:,}", f"{updated:,}")
    return added, updated


def cleared_employers(df: pd.DataFrame) -> dict[str, set[str]]:
    """FEC's copy must not restore a spouse's firm."""
    if not (MANUAL_EMPLOYER_OVERRIDES_CSV.exists() and RAW_CSV.exists()):
        return {}
    with MANUAL_EMPLOYER_OVERRIDES_CSV.open(encoding="utf-8", newline="") as handle:
        cleared = {
            (row.get("sub_id") or "").strip() for row in csv.DictReader(handle)
            if (row.get("contributor_employer") or "").strip().upper() == "[CLEAR]"
        }
    if not cleared:
        return {}
    raw = pd.read_csv(RAW_CSV, usecols=["sub_id", "contributor_employer"], dtype=str, keep_default_na=False)
    raw = raw[raw["sub_id"].isin(cleared)]
    keys = df.assign(sub_id=df["sub_id"].astype(str)).set_index("sub_id")["donor_key"]
    out: dict[str, set[str]] = {}
    for sub_id, employer in zip(raw["sub_id"], raw["contributor_employer"]):
        name = normalize_previous_employer_value(employer).upper()
        if name and sub_id in keys.index:
            out.setdefault(str(keys.loc[sub_id]), set()).add(name)
    return out
