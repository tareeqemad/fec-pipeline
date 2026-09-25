"""Normalize names, employers and committee names for matching."""
import re

from fec.donor_match.constants import (
    NAME_SUFFIXES,
)
from fec.donor_match.scoring import _NAME_TOKEN_RE

# stripped in this order -- " CO" runs before " CORP" on purpose, and
# str.replace hits mid-string too (" CO" also eats the start of " CONSULTING":
# ACME CONSULTING -> ACMENSULTING). Both quirks are load-bearing: these strings
# feed employer-match scoring and thus donor identity. Do not change.
_EMPLOYER_STRIP = (" LLC", " INC", " INC.", " LLP", " LP", " CO", " CO.",
                   " CORP", " CORP.", " LTD", " LTD.", ",", ".", "'")


# a trailing title (REP/SEN/DR/JR/...) at the end of a name: "JOHN SMITH, JR."
_COMM_SUFFIXES_RE = re.compile(
    r'\b(?:REP|SEN|MR|MRS|MS|DR|JR|SR|HON|HONORABLE)'
    r'\.?\s*$',
    re.IGNORECASE,
)


_GENERATION_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})


# pull a JR/SR/roman-numeral suffix out of a name
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


# normalize a name to LAST|FIRST for matching
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


# extract middle name/initial from a full name
def extract_middle(name: str) -> str:
    """Extract middle name/initial from full name."""
    name = str(name or "").strip().upper()
    if "," not in name:
        return ""
    first_raw = name.split(",", 1)[1].strip()
    parts = first_raw.replace(".", " ").split()
    parts = [p for p in parts if p not in NAME_SUFFIXES and not p.startswith("(")]
    return parts[1] if len(parts) > 1 else ""


# normalize an employer name for comparison
def normalize_employer(emp: str) -> str:
    """Normalize employer name for comparison."""
    emp = str(emp or "").strip().upper()
    for suffix in _EMPLOYER_STRIP:
        emp = emp.replace(suffix, "")
    return emp.strip()


# normalize a committee contributor name to ORG|FIRST
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
