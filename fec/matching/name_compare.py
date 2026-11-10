"""Normalize fields and compare name variants for donor matching."""

import re

from rapidfuzz.distance import Levenshtein

from fec.cleaning.work.company_names import employer_rule_key
from fec.vocabulary.tables import CONFIG_DIR, read_map, read_rows

_NAME_TOKEN_RE = re.compile(r"[A-Z0-9]+")


NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD", "DDS", "CPA", "DO"}


# First names that look alike but name different people (DAN / DANA).
_BLOCKED_FIRST_PAIRS = {
    frozenset((row["name_a"], row["name_b"]))
    for row in read_rows(CONFIG_DIR / "different_first_names.csv")
}


NICKNAME_MAP = read_map(CONFIG_DIR / "nicknames.csv")

# Keep replacement order: donor keys depend on these comparisons.
_EMPLOYER_STRIP = (
    " LLC",
    " INC",
    " INC.",
    " LLP",
    " LP",
    " CO",
    " CO.",
    " CORP",
    " CORP.",
    " LTD",
    " LTD.",
    ",",
    ".",
    "'",
)


# A trailing title at the end of a name.
_COMM_SUFFIXES_RE = re.compile(
    r"\b(?:REP|SEN|MR|MRS|MS|DR|JR|SR|HON|HONORABLE)"
    r"\.?\s*$",
    re.IGNORECASE,
)


GENERATION_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})


def extract_generational_suffix(name: str) -> str:
    """Return a JR/SR/roman suffix from either side of the comma."""
    text = str(name or "").strip().upper()
    last, comma, first = text.partition(",")
    parts = (last, first) if comma else (text,)
    found = {
        tokens[-1]
        for part in parts
        if (tokens := _NAME_TOKEN_RE.findall(part)) and tokens[-1] in GENERATION_SUFFIXES
    }
    return next(iter(found)) if len(found) == 1 else ""


def filed_name_words(name: str) -> frozenset:
    """Every word of the filed name, initials included, whatever the order."""
    words = _NAME_TOKEN_RE.findall(str(name or "").upper())
    return frozenset(word for word in words if word not in NAME_SUFFIXES)


def normalize_name(name: str) -> str:
    """Normalize to LAST|FIRST (drop middle, suffixes, punctuation)."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return name

    parts = name.split(",", 1)
    last = parts[0].strip()
    first_raw = parts[1].strip()

    last_words = last.split()
    last_words = [word for word in last_words if word not in NAME_SUFFIXES]
    last = " ".join(last_words).replace(".", "").replace("'", "").strip()

    first_parts = first_raw.replace(".", " ").split()
    first_parts = [part for part in first_parts if part not in NAME_SUFFIXES]
    first = first_parts[0] if first_parts else ""
    if first.startswith("("):
        first = first_parts[1] if len(first_parts) > 1 else ""

    return f"{last}|{first}"


def extract_middle(name: str) -> str:
    """Extract middle name/initial from full name."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return ""
    first_raw = name.split(",", 1)[1].strip()
    parts = first_raw.replace(".", " ").split()
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
    name = str(name or "").strip().upper()
    if "," not in name:
        return name.replace(".", "").replace(" ", "").strip()

    parts = name.split(",", 1)
    org = parts[0].strip().replace(".", "").replace(" ", "").strip()
    person = parts[1].strip()

    person = _COMM_SUFFIXES_RE.sub("", person).strip()
    person = person.replace(".", "").strip()
    person_parts = person.split()
    first = person_parts[0] if person_parts else ""

    return f"{org}|{first}"


def first_names_may_match(
    norm_name_a: str, norm_name_b: str, surname_firsts: set[str] | None = None
) -> bool:
    """Tell whether two names share a surname with nickname, near-spelling or truncated first names.

    surname_firsts holds every first name filed with this surname: a short
    form that begins two different names there (CHRIS beside CHRISTINE and
    CHRISTOPHER) names nobody in particular and is not a candidate.
    """
    if "|" not in norm_name_a or "|" not in norm_name_b:
        return False
    last_a, first_a = norm_name_a.split("|", 1)
    last_b, first_b = norm_name_b.split("|", 1)

    if last_a != last_b:
        return False
    if first_a == first_b:
        return False
    if not first_a or not first_b:
        return False

    pair = frozenset((first_a.upper(), first_b.upper()))
    if pair in _BLOCKED_FIRST_PAIRS:
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
    # Truncated further: TIM / TIMOTHY, BRAD / BRADLEY, ALLI / ALLISON.
    longer = {
        NICKNAME_MAP.get(name, name)
        for name in (surname_firsts or {long})
        if name != short and name.startswith(short)
    }
    return len(longer) == 1


def _squash(surname: str) -> str:
    """Strip spaces, hyphens and dots for surname comparison."""
    return surname.replace(" ", "").replace("-", "").replace(".", "")


def is_surname_variant(last_a: str, last_b: str) -> bool:
    """Surname spelling variants; the phase still demands strong geography."""
    if not last_a or not last_b or last_a == last_b:
        return False
    if _squash(last_a) == _squash(last_b):  # DE TOLEDO == DETOLEDO
        return True
    distance = Levenshtein.distance(last_a, last_b)
    shortest_length = min(len(last_a), len(last_b))
    if shortest_length < 4:  # short surnames: single-char slip only
        return distance == 1
    if distance <= 2:  # KHODAR/KHODARI (1), A SPIEGEL/SPIEGEL (2)
        return True
    shorter, longer = sorted((last_a, last_b), key=len)  # truncation: DOLNER/DOLINER
    return longer.startswith(shorter) and len(shorter) >= 4 and (len(longer) - len(shorter)) <= 3
