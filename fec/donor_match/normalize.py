"""Normalize fields and compare name variants for donor matching."""
import re

from fec.cleaning._helpers import levenshtein
from fec.cleaning.work.employer_aliases import employer_rule_key
from fec.config.tables import CONFIG_DIR, read_map


_NAME_TOKEN_RE = re.compile(r"[A-Z0-9]+")


NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "ESQ", "MD", "PHD", "DDS", "CPA", "DO"}


# blocked first-name pairs: look similar but are different people
_BLOCKED_FIRST_PAIRS = {frozenset(p) for p in [
    ('DAN', 'DANA'),        ('DANIEL', 'DANA'),
    ('BETH', 'SETH'),
    ('JOAN', 'JOHN'),
    ('ANDREA', 'ANDREW'),   ('ANDREA', 'ANDRE'),
    ('ALLEN', 'ELLEN'),     ('ALAN', 'ELENA'),
    ('RANDI', 'RANDY'),
    ('ROBIN', 'ROB'),       ('ROBIN', 'ROBERT'),
    ('SANDY', 'ANDY'),      ('SANDRA', 'ANDREW'),
    ('CARL', 'CARI'),
    ('EVAN', 'IVAN'),
    ('MARK', 'MARY'),       ('MARC', 'MARY'),
    ('KEREN', 'KAREN'),
    ('EARL', 'CARL'),
    ('JOY', 'JAY'),
    ('DAWN', 'DAN'),
    ('JANE', 'JUNE'),
    ('JULIE', 'JULIA'),
    ('ANN', 'DAN'),
    ('BARRY', 'LARRY'),     ('BARRY', 'HARRY'),
    ('LARRY', 'HARRY'),     ('TERRY', 'JERRY'),
    ('TERRY', 'PERRY'),     ('TERRY', 'KERRY'),
    ('JERRY', 'PERRY'),     ('JERRY', 'KERRY'),
    ('LOREN', 'OREN'),
    ('ARI', 'AVI'),         ('ARI', 'URI'),
    ('ETAN', 'EVAN'),
    ('KEN', 'BEN'),         ('KEN', 'LEN'),
    ('BEN', 'LEN'),
    ('ROY', 'ROB'),         ('ROY', 'RON'),
    ('GARY', 'CARY'),
]}


NICKNAME_MAP = read_map(CONFIG_DIR / "nicknames.csv")

# Keep replacement order: donor keys depend on these comparisons.
_EMPLOYER_STRIP = (" LLC", " INC", " INC.", " LLP", " LP", " CO", " CO.",
                   " CORP", " CORP.", " LTD", " LTD.", ",", ".", "'")


# A trailing title at the end of a name.
_COMM_SUFFIXES_RE = re.compile(
    r'\b(?:REP|SEN|MR|MRS|MS|DR|JR|SR|HON|HONORABLE)'
    r'\.?\s*$',
    re.IGNORECASE,
)


_GENERATION_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})


def extract_generational_suffix(name: str) -> str:
    """Return a JR/SR/roman suffix from either side of the comma."""
    text = str(name or "").strip().upper()
    last, comma, first = text.partition(",")
    parts = (last, first) if comma else (text,)
    found = {
        tokens[-1]
        for part in parts
        if (tokens := _NAME_TOKEN_RE.findall(part))
        and tokens[-1] in _GENERATION_SUFFIXES
    }
    return next(iter(found)) if len(found) == 1 else ""


def normalize_name(name: str) -> str:
    """Normalize to LAST|FIRST (drop middle, suffixes, punctuation)."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return name

    parts = name.split(",", 1)
    last = parts[0].strip()
    first_raw = parts[1].strip()

    last_words = last.split()
    last_words = [w for w in last_words if w not in NAME_SUFFIXES]
    last = " ".join(last_words).replace(".", "").replace("'", "").strip()

    first_parts = first_raw.replace(".", " ").split()
    first_parts = [p for p in first_parts if p not in NAME_SUFFIXES]
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
    parts = [p for p in parts if p not in NAME_SUFFIXES and not p.startswith("(")]
    return parts[1] if len(parts) > 1 else ""


def normalize_employer(emp: str) -> str:
    """Normalize employer name for comparison."""
    emp = employer_rule_key(str(emp or ""))
    for suffix in _EMPLOYER_STRIP:
        emp = emp.replace(suffix, "")
    return emp.strip()


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


def _are_cross_group_candidates(norm1: str, norm2: str) -> bool:
    """Same surname with nickname, near-spelling or truncated first names."""
    if "|" not in norm1 or "|" not in norm2:
        return False
    last1, first1 = norm1.split("|", 1)
    last2, first2 = norm2.split("|", 1)

    if last1 != last2:
        return False
    if first1 == first2:
        return False
    if not first1 or not first2:
        return False

    pair = frozenset((first1.upper(), first2.upper()))
    if pair in _BLOCKED_FIRST_PAIRS:
        return False

    if NICKNAME_MAP.get(first1, first1) == NICKNAME_MAP.get(first2, first2):
        return True

    dist = levenshtein(first1, first2)
    if dist <= 1 and len(first1) >= 3 and len(first2) >= 3:
        return True

    if dist == 2:
        short, long = (first1, first2) if len(first1) <= len(first2) else (first2, first1)
        if long.startswith(short) and len(short) >= 3:
            return True

    return False


def _squash(s: str) -> str:
    """Strip spaces, hyphens and dots for surname comparison."""
    return s.replace(" ", "").replace("-", "").replace(".", "")


def _is_surname_variant(last1: str, last2: str) -> bool:
    """Surname spelling variants; the phase still demands strong geography."""
    if not last1 or not last2 or last1 == last2:
        return False
    if _squash(last1) == _squash(last2):        # DE TOLEDO == DETOLEDO
        return True
    d = levenshtein(last1, last2)
    m = min(len(last1), len(last2))
    if m < 4:                                   # short surnames: single-char slip only
        return d == 1
    if d <= 2:                                  # KHODAR/KHODARI (1), A SPIEGEL/SPIEGEL (2)
        return True
    lo, hi = sorted((last1, last2), key=len)    # truncation: DOLNER/DOLINER
    return hi.startswith(lo) and len(lo) >= 4 and (len(hi) - len(lo)) <= 3
