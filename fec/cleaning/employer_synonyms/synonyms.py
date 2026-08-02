"""EMPLOYER_SYNONYMS assembly: merge fragments, load overrides, expand aliases, flatten chains."""
import json
import re
from pathlib import Path

from fec.config.constants import LEGAL_SUFFIX_RE as _LEGAL_SUFFIX_RE
from fec.log import get_logger

from fec.cleaning.employer_synonyms.synonyms_table_1 import SYNONYMS as _TABLE_1
from fec.cleaning.employer_synonyms.synonyms_table_2 import SYNONYMS as _TABLE_2

logger = get_logger(__name__)

# Built from donor-overlap analysis; only safe merges where the same donors
# used both spellings. Fragment order preserves the original insertion order.
EMPLOYER_SYNONYMS = {**_TABLE_1, **_TABLE_2}


def _load_manual_overrides():
    """Merge human-reviewed mappings from data/manual_typo_overrides.json into EMPLOYER_SYNONYMS."""
    path = Path(__file__).resolve().parents[3] / 'data' / 'manual_typo_overrides.json'
    if not path.exists():
        return 0
    # a corrupt curated file must fail loudly, not silently drop every override
    with open(path, encoding='utf-8') as handle:
        overrides = json.load(handle)
    count = 0
    for key, value in overrides.items():
        if isinstance(key, str) and isinstance(value, str):
            EMPLOYER_SYNONYMS[key.strip().upper()] = value.strip()
            count += 1
    return count


def _flatten_synonym_chains() -> int:
    """Rewrite each key to the end of its chain - apply_employer_synonyms maps once, so A->B, B->C would strand A at B; cycles stay at one hop."""
    count = 0
    for key in list(EMPLOYER_SYNONYMS):
        seen = {key}
        val = EMPLOYER_SYNONYMS[key]
        while val in EMPLOYER_SYNONYMS and val not in seen:
            seen.add(val)
            val = EMPLOYER_SYNONYMS[val]
        if val in EMPLOYER_SYNONYMS:
            logger.warning("Cycle in EMPLOYER_SYNONYMS - %r left at one hop", key)
            continue
        if val != EMPLOYER_SYNONYMS[key]:
            EMPLOYER_SYNONYMS[key] = val
            count += 1
    return count


def _canonicalize_for_match(s: str) -> str:
    """Reduce a string to what apply_employer_synonyms sees at match time; mirrors the regex chain in normalize_employer_canonical - keep in sync."""
    s = (s or '').upper().strip()
    s = re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', s)
    s = _LEGAL_SUFFIX_RE.sub('', s)
    s = re.sub(r'[&$]', ' AND ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.rstrip('.,').strip()


def _expand_synonym_keys() -> int:
    """Register each key's post-normalize form as an alias, since matching happens after normalize_employer_canonical has run."""
    added = 0
    for key in list(EMPLOYER_SYNONYMS):
        target = EMPLOYER_SYNONYMS[key]
        norm_key = _canonicalize_for_match(key)
        # skip identity and self-maps: that form is already canonical
        if not norm_key or norm_key == key or norm_key == target:
            continue
        existing = EMPLOYER_SYNONYMS.get(norm_key)
        if existing is not None:
            if existing != target:
                # two raw keys normalize to the same alias; keep the first
                logger.debug(
                    "Synonym alias collision on %r - keeping existing target", norm_key)
            continue
        EMPLOYER_SYNONYMS[norm_key] = target
        added += 1
    return added


# Runs at import time so the rest of the pipeline sees the merged dict
# transparently; DEBUG because it precedes the run's user-facing output.
_n_manual = _load_manual_overrides()
_n_alias = _expand_synonym_keys()
_n_flat = _flatten_synonym_chains()
logger.debug(
    "employer synonyms ready: %s manual overrides, %s key aliases, %s chains flattened",
    f"{_n_manual:,}", _n_alias, _n_flat,
)
