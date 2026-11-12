"""Normalize fields and compare name variants for donor matching."""

import re

from rapidfuzz.distance import Levenshtein

from fec.cleaning.work.company_names import employer_rule_key
from fec.vocabulary.tables import CONFIG_DIR, ordered_words, read_map, read_rows, words

_NAME_TOKEN_RE = re.compile(r"[A-Z0-9]+")
GENERATION_SUFFIXES = words("generation_suffixes")
NAME_SUFFIXES = GENERATION_SUFFIXES | words("credential_suffixes")
NICKNAME_MAP = read_map(CONFIG_DIR / "nicknames.csv")
# First names that look alike but name different people (DAN / DANA).
_BLOCKED_FIRST_PAIRS = {
    frozenset((row["name_a"], row["name_b"])) for row in read_rows(CONFIG_DIR / "different_first_names.csv")
}
# Keep replacement order: donor keys depend on these comparisons.
_EMPLOYER_STRIP = ordered_words("matching_employer_strip")
# A trailing title at the end of a name.
_COMM_SUFFIXES_RE = re.compile(r"\b(?:REP|SEN|MR|MRS|MS|DR|JR|SR|HON|HONORABLE)\.?\s*$", re.IGNORECASE)


def extract_generational_suffix(name: str) -> str:
    """Return a JR/SR/roman suffix from either side of the comma."""
    last, _, first = str(name or "").strip().upper().partition(",")
    found = set()
    for part in (last, first):
        tokens = _NAME_TOKEN_RE.findall(part)
        if tokens and tokens[-1] in GENERATION_SUFFIXES:
            found.add(tokens[-1])
    return found.pop() if len(found) == 1 else ""


def filed_name_words(name: str) -> frozenset:
    """Every word of the filed name, initials included, whatever the order."""
    tokens = _NAME_TOKEN_RE.findall(str(name or "").upper())
    return frozenset(token for token in tokens if token not in NAME_SUFFIXES)


def normalize_name(name: str) -> str:
    """Normalize to LAST|FIRST (drop middle, suffixes, punctuation)."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return name
    last, first_raw = name.split(",", 1)
    last_words = [word for word in last.split() if word not in NAME_SUFFIXES]
    last = " ".join(last_words).replace(".", "").replace("'", "").strip()
    first_parts = [part for part in first_raw.replace(".", " ").split() if part not in NAME_SUFFIXES]
    first = first_parts[0] if first_parts else ""
    if first.startswith("("):
        first = first_parts[1] if len(first_parts) > 1 else ""
    return f"{last}|{first}"


def extract_middle(name: str) -> str:
    """Extract middle name/initial from full name."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return ""
    parts = name.split(",", 1)[1].replace(".", " ").split()
    parts = [part for part in parts if part not in NAME_SUFFIXES and not part.startswith("(")]
    return parts[1] if len(parts) > 1 else ""


def normalize_employer(employer: str) -> str:
    """Normalize employer name for comparison."""
    employer = employer_rule_key(str(employer or ""))
    for suffix in _EMPLOYER_STRIP:
        employer = employer.replace(suffix, "")
    return employer.strip()


def normalize_committee_name(name: str) -> str:
    """Normalize a committee contributor name to ORG|FIRST for deduplication."""
    org, comma, person = str(name or "").strip().upper().partition(",")
    org = org.replace(".", "").replace(" ", "").strip()
    if not comma:
        return org
    person_words = _COMM_SUFFIXES_RE.sub("", person).replace(".", "").split()
    first = person_words[0] if person_words else ""
    return f"{org}|{first}"


def first_names_may_match(norm_name_a: str, norm_name_b: str, surname_firsts: set[str]) -> bool:
    """Tell whether two different LAST|FIRST names of one surname have nickname, near or truncated first names."""
    first_a = norm_name_a.split("|", 1)[1]
    first_b = norm_name_b.split("|", 1)[1]
    if not first_a or not first_b or frozenset((first_a, first_b)) in _BLOCKED_FIRST_PAIRS:
        return False
    if NICKNAME_MAP.get(first_a, first_a) == NICKNAME_MAP.get(first_b, first_b):
        return True
    distance = Levenshtein.distance(first_a, first_b)
    if distance <= 1 and len(first_a) >= 3 and len(first_b) >= 3:
        return True
    short, long = (first_a, first_b) if len(first_a) <= len(first_b) else (first_b, first_a)
    if len(short) < 3 or not long.startswith(short):
        return False
    if distance == 2:
        return True
    # Truncated further (TIM / TIMOTHY) only when the short form begins one name filed with this surname.
    longer = {NICKNAME_MAP.get(name, name) for name in surname_firsts if name != short and name.startswith(short)}
    return len(longer) == 1


def _squash(surname: str) -> str:
    return surname.replace(" ", "").replace("-", "").replace(".", "")


def is_surname_variant(last_a: str, last_b: str) -> bool:
    """Tell whether two different, non-empty surnames are spelling variants; the phase demands strong geography."""
    if _squash(last_a) == _squash(last_b):  # DE TOLEDO == DETOLEDO
        return True
    distance = Levenshtein.distance(last_a, last_b)
    if min(len(last_a), len(last_b)) < 4:  # short surnames: single-char slip only
        return distance == 1
    if distance <= 2:  # KHODAR/KHODARI (1), A SPIEGEL/SPIEGEL (2)
        return True
    shorter, longer = sorted((last_a, last_b), key=len)  # truncation: DOLNER/DOLINER
    return longer.startswith(shorter) and (len(longer) - len(shorter)) <= 3
