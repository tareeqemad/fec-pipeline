"""
Manual employer- and committee-address overrides.

Loads human-curated addresses from CSV files into the resolve caches as
`method='manual_override'` entries with HIGH confidence.

Why a CSV instead of editing the cache directly:
    - Caches are regenerated on every full pipeline reset
    - The CSV is the persistent source of truth — survives cache wipes
    - Easy to review/edit in a spreadsheet or PR diff

Why method='manual_override':
    - `_needs_ai()` treats these as final; the AI step never re-asks
    - `_score()` in dedup.py ranks them above the ai_* methods

Two files, two caches:
    manual_employer_addresses.csv  -> employer cache, keyed by EMPLOYER name
    manual_committee_addresses.csv -> committee cache, keyed by NAME|STATE
"""
import csv
from pathlib import Path
from typing import Callable, Tuple

from fec.log import get_logger

logger = get_logger(__name__)

_CHANGE_FIELDS = ('employer_address', 'employer_city',
                  'employer_state', 'employer_zip', 'method')


def _load_overrides(csv_path: Path, cache, key_fn: Callable[[dict], str],
                    label: str) -> Tuple[int, int]:
    """Inject manual address overrides from a CSV into a resolve cache.

    key_fn(row) builds the cache key for a CSV row (return None/'' to skip it).
    Returns (n_added, n_updated)."""
    if not csv_path.exists():
        logger.info(f"    {label}: {csv_path.name} not found — skipping")
        return 0, 0

    n_added = n_updated = 0
    with open(csv_path, encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            key = key_fn(row)
            if not key:
                continue
            address = (row.get('address') or '').strip()
            city = (row.get('city') or '').strip()
            state = (row.get('address_state') or row.get('state') or '').strip().upper()
            # A street address is ideal, but a deliberate city/state-only override
            # is also valid — apply.py honors a manual_override with no street
            # (e.g. a firm whose US-office city is known but exact street isn't).
            if not address and not (city and state):
                continue  # nothing usable — skip
            entry = {
                'employer_address': address,
                'employer_city':    city,
                'employer_state':   state,
                'employer_zip':     (row.get('zip') or '').strip(),
                'method':           'manual_override',
                'confidence':       'HIGH',
            }
            existing = cache.get(key)
            if existing is None:
                n_added += 1
            elif any(existing.get(k, '') != entry[k] for k in _CHANGE_FIELDS):
                n_updated += 1
            else:
                continue  # identical entry already present — no noisy diff
            cache.put(key, entry)

    if n_added or n_updated:
        cache.save()
    logger.info(f"    {label}: {n_added:,} added, {n_updated:,} updated "
                f"from {csv_path.name}")
    return n_added, n_updated


def load_manual_overrides(csv_path: Path, addr_cache) -> Tuple[int, int]:
    """Manual employer-HQ overrides — keyed by employer name (uppercased)."""
    return _load_overrides(
        csv_path, addr_cache,
        key_fn=lambda r: (r.get('name') or '').strip().upper(),
        label="manual overrides",
    )


def load_manual_committee_overrides(csv_path: Path, comm_cache) -> Tuple[int, int]:
    """Manual committee/organization overrides.

    Keyed by NAME|STATE — the exact key step_committees_ai uses
    (contributor_name | contributor_state). Use this for entities in the
    COMMITTEE/PAC bucket whose address is curated by hand — including the
    businesses that bucket lumps in (LLCs, holding companies, etc.)."""
    def _key(r):
        name = (r.get('committee_name') or '').strip()
        if not name:
            return None
        state = (r.get('contributor_state') or '').strip()
        return f"{name}|{state}"
    return _load_overrides(csv_path, comm_cache, key_fn=_key,
                           label="manual committee overrides")
