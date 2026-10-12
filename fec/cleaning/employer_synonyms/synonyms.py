"""Load and prepare employer spelling rules."""
import csv
import re

from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.env import EMPLOYER_NAME_RULES_CSV
from fec.log import get_logger

logger = get_logger(__name__)


# read employer rename rules from csv
def _load_rules() -> dict[str, str]:
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


# flatten chains; synonyms map once, cycles stay
def _flatten_synonym_chains() -> int:
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


# mirror normalize_employer_canonical; keep in sync
def _canonicalize_for_match(s: str) -> str:
    s = (s or '').upper().strip()
    s = re.sub(r'\s*\([A-Z]{1,5}\)\s*$', '', s)
    s = LEGAL_SUFFIX_RE.sub('', s)
    s = re.sub(r'[&$]', ' AND ', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.rstrip('.,').strip()


# add each key's normalized form as alias
def _expand_synonym_keys() -> int:
    added = 0
    for key in list(EMPLOYER_SYNONYMS):
        target = EMPLOYER_SYNONYMS[key]
        norm_key = _canonicalize_for_match(key)
        # identity and self-maps are already canonical
        if not norm_key or norm_key in (key, target):
            continue
        existing = EMPLOYER_SYNONYMS.get(norm_key)
        if existing is not None:
            if existing != target:
                # alias collision keeps the first target
                logger.debug(
                    "Synonym alias collision on %r - keeping existing target", norm_key)
            continue
        EMPLOYER_SYNONYMS[norm_key] = target
        added += 1
    return added


# runs at import, before user-facing output
_n_alias = _expand_synonym_keys()
_n_flat = _flatten_synonym_chains()
logger.debug(
    "employer synonyms ready: %s rules, %s aliases, %s chains flattened",
    f"{len(EMPLOYER_SYNONYMS):,}", _n_alias, _n_flat,
)
