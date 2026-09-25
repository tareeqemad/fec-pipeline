"""Load and prepare employer spelling rules."""
import csv
import re

from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.env import EMPLOYER_NAME_RULES_CSV
from fec.log import get_logger

logger = get_logger(__name__)


def _load_rules() -> dict[str, str]:
    """Read the single rules file."""
    if not EMPLOYER_NAME_RULES_CSV.exists():
        raise FileNotFoundError(
            f"Missing employer rules: {EMPLOYER_NAME_RULES_CSV}"
        )

    rules = {}
    with EMPLOYER_NAME_RULES_CSV.open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            variant = (row.get("variant") or "").strip().upper()
            canonical = (row.get("canonical") or "").strip()
            source = (row.get("source") or "").strip()
            if not variant or not canonical or not source:
                raise ValueError(
                    f"Invalid employer rule on row {number}"
                )
            rules[variant] = canonical
    return rules


EMPLOYER_SYNONYMS = _load_rules()


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
    s = LEGAL_SUFFIX_RE.sub('', s)
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
        if not norm_key or norm_key in (key, target):
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
_n_alias = _expand_synonym_keys()
_n_flat = _flatten_synonym_chains()
logger.debug(
    "employer synonyms ready: %s rules, %s aliases, %s chains flattened",
    f"{len(EMPLOYER_SYNONYMS):,}", _n_alias, _n_flat,
)
