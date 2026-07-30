"""Load human-curated addresses from CSV into the resolve caches as method='manual_override' - the CSV survives cache wipes, _needs_ai treats these as final, and dedup ranks them above ai_*."""
import csv
from collections.abc import Callable
from pathlib import Path

from fec.log import get_logger

logger = get_logger(__name__)

_CHANGE_FIELDS = ('employer_address', 'employer_city',
                  'employer_state', 'employer_zip', 'method')


def _load_overrides(csv_path: Path, cache, key_fn: Callable[[dict], str],
                    label: str) -> tuple[int, int]:
    """Inject manual overrides from a CSV into a cache; key_fn(row) builds the key (falsy skips the row). Returns (n_added, n_updated)."""
    if not csv_path.exists():
        logger.info(f"    {label}: {csv_path.name} not found - skipping")
        return 0, 0

    n_added = n_updated = 0
    with open(csv_path, encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle):
            key = key_fn(row)
            if not key:
                continue
            address = (row.get('address') or '').strip()
            city = (row.get('city') or '').strip()
            state = (row.get('address_state') or row.get('state') or '').strip().upper()
            # A deliberate city/state-only override is valid - apply.py honors
            # a manual_override with no street.
            if not address and not (city and state):
                continue
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
            elif any(existing.get(field, '') != entry[field] for field in _CHANGE_FIELDS):
                n_updated += 1
            else:
                continue  # identical entry already present - no noisy diff
            cache.put(key, entry)

    if n_added or n_updated:
        cache.save()
    logger.info(f"    {label}: {n_added:,} added, {n_updated:,} updated "
                f"from {csv_path.name}")
    return n_added, n_updated


def load_manual_overrides(csv_path: Path, addr_cache) -> tuple[int, int]:
    """Manual employer-HQ overrides - keyed by employer name (uppercased)."""
    return _load_overrides(
        csv_path, addr_cache,
        key_fn=lambda row: (row.get('name') or '').strip().upper(),
        label="manual overrides",
    )


def load_manual_committee_overrides(csv_path: Path, comm_cache) -> tuple[int, int]:
    """Manual overrides for the COMMITTEE/PAC bucket (incl. the LLCs it lumps in), keyed NAME|STATE - the exact key the committee steps use."""
    def _key(row):
        name = (row.get('committee_name') or '').strip()
        if not name:
            return None
        state = (row.get('contributor_state') or '').strip()
        return f"{name}|{state}"
    return _load_overrides(csv_path, comm_cache, key_fn=_key,
                           label="manual committee overrides")
