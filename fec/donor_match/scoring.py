"""How two records are compared: string normalization, then the scoring engine."""

import re

from fec.cleaning._helpers import levenshtein

from .constants import (
    NAME_SUFFIXES,
    NICKNAME_MAP, _BLOCKED_FIRST_PAIRS, MERGE_THRESHOLD,
    SCORE_SAME_STREET, SCORE_SAME_EMPLOYER, SCORE_SAME_STATE,
    SCORE_SAME_CITY, SCORE_SAME_ZIP5, SCORE_SAME_ZIP3,
    SCORE_MIDDLE_MATCH, SCORE_MIDDLE_PARTIAL, SCORE_MIDDLE_CONFLICT,
    SCORE_RARE_NAME, SCORE_VERY_RARE_BONUS, SCORE_COMMON_PENALTY,
    SCORE_SAME_OCC, SCORE_OCC_RETIRED,
    SCORE_RETIRED_NO_EMP, SCORE_RARE_EMPLOYER_OK,
)

# -- string normalization: make raw strings comparable ----------------------

# stripped in this order -- " CO" runs before " CORP" on purpose, and
# str.replace hits mid-string too (" CO" also eats the start of " CONSULTING":
# ACME CONSULTING -> ACMENSULTING). Both quirks are load-bearing: these strings
# feed employer-match scoring and thus donor identity. Do not change.
_EMPLOYER_STRIP = (" LLC", " INC", " INC.", " LLP", " LP", " CO", " CO.",
                   " CORP", " CORP.", " LTD", " LTD.", ",", ".", "'")

_COMM_SUFFIXES_RE = re.compile(
    r'\b(?:REP|SEN|MR|MRS|MS|DR|JR|SR|HON|HONORABLE)'
    r'\.?\s*$',
    re.IGNORECASE,
)

_GENERATION_SUFFIXES = frozenset({"JR", "SR", "II", "III", "IV", "V"})
_NAME_TOKEN_RE = re.compile(r"[A-Z0-9]+")


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
    emp = str(emp or "").strip().upper()
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


# -- the scoring engine ------------------------------------------------------


def _shared_evidence(p1: dict, p2: dict) -> tuple:
    """Score shared address and employer facts."""
    score = 0
    signals = []
    has_geo = False

    if p1["streets"] and p2["streets"]:
        common = p1["streets"] & p2["streets"]
        if common:
            score += SCORE_SAME_STREET
            signals.append(f"street(+{SCORE_SAME_STREET})")
            has_geo = True

    common_employers = p1["norm_employers"] & p2["norm_employers"]
    if common_employers:
        score += SCORE_SAME_EMPLOYER
        employer = list(common_employers)[0][:25]
        signals.append(f"employer={employer}(+{SCORE_SAME_EMPLOYER})")

    if p1["state"] and p2["state"] and p1["state"] == p2["state"]:
        score += SCORE_SAME_STATE
        signals.append(f"state={p1['state']}(+{SCORE_SAME_STATE})")
        has_geo = True

    if p1["city"] and p2["city"] and p1["city"] == p2["city"]:
        score += SCORE_SAME_CITY
        signals.append(f"city(+{SCORE_SAME_CITY})")

    z1, z2 = p1["zip5"], p2["zip5"]
    if z1 and z2 and len(z1) == 5 and len(z2) == 5:
        if z1 == z2:
            score += SCORE_SAME_ZIP5
            signals.append(f"zip5={z1}(+{SCORE_SAME_ZIP5})")
            has_geo = True
        elif z1[:3] == z2[:3]:
            score += SCORE_SAME_ZIP3
            signals.append(f"zip3={z1[:3]}(+{SCORE_SAME_ZIP3})")
            has_geo = True

    return score, signals, has_geo, common_employers


def _occupation_evidence(p1: dict, p2: dict) -> tuple:
    """Score a consistent career or a career-to-retirement transition."""
    score = 0
    signals = []
    c1, c2 = p1.get("occ_categories", set()), p2.get("occ_categories", set())
    r1, r2 = p1.get("retired", False), p2.get("retired", False)
    if c1 and c2 and (c1 & c2):
        score += SCORE_SAME_OCC
        signals.append(f"occ={sorted(c1 & c2)[0][:18]}(+{SCORE_SAME_OCC})")
    elif (c1 and r2) or (c2 and r1):
        score += SCORE_OCC_RETIRED
        signals.append(f"occ_retired_transition(+{SCORE_OCC_RETIRED})")

    return score, signals


def _middle_name_evidence(m1: str, m2: str) -> tuple:
    """Score middle names and hard-block two different full names."""
    score = 0
    signals = []
    hard_block = False

    if m1 and m2:
        if m1 == m2:
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_ok={m1}(+{SCORE_MIDDLE_MATCH})")
        elif (len(m1) == 1 and m2[0] == m1) or (len(m2) == 1 and m1[0] == m2):
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_ok={m1}/{m2}(+{SCORE_MIDDLE_MATCH})")
        elif len(m1) == 1 and len(m2) == 1:
            score += SCORE_MIDDLE_CONFLICT
            signals.append(f"MIDDLE_CONFLICT={m1}\u2260{m2}({SCORE_MIDDLE_CONFLICT})")
        elif levenshtein(m1, m2) == 1:
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_typo={m1}/{m2}(+{SCORE_MIDDLE_MATCH})")
        else:
            signals.append(f"HARD_BLOCK={m1}\u2260{m2} (different people)")
            hard_block = True
    elif m1 or m2:
        score += SCORE_MIDDLE_PARTIAL
        signals.append(f"middle_partial(+{SCORE_MIDDLE_PARTIAL})")

    return score, signals, hard_block


def _is_eponymous(p: dict, common_employers: set) -> bool:
    """Return whether a shared employer contains the donor's surname."""
    name = p.get("name", "")
    surname = name.split(",")[0].strip().upper() if "," in name else ""
    return (
        len(surname) >= 3
        and any(surname in employer.upper() for employer in common_employers)
    )


def _apply_no_geo_safety(
    score: int,
    signals: list,
    p1: dict,
    name_freq: int,
    common_employers: set,
) -> int:
    """Require a shared employer when geography is absent."""
    if common_employers:
        if name_freq <= 3:
            score += SCORE_RARE_EMPLOYER_OK
            signals.append(f"rare_employer_ok(freq={name_freq},+{SCORE_RARE_EMPLOYER_OK})")
        elif _is_eponymous(p1, common_employers) and name_freq <= 10:
            signals.append(f"eponymous_employer(freq={name_freq})")
        elif name_freq <= 10:
            score = min(score, MERGE_THRESHOLD - 1)
            signals.append(f"EMPLOYER_ONLY_NO_GEO(capped\u2192{score},freq={name_freq})")
        else:
            score = min(score, MERGE_THRESHOLD - 20)
            signals.append(f"EMPLOYER_ONLY_COMMON(capped\u2192{score},freq={name_freq})")
        return score

    if name_freq <= 2:
        score = min(score, MERGE_THRESHOLD - 1)
        signals.append(f"NO_CORROBORATION_RARE(capped\u2192{score})")
    else:
        score = min(score, MERGE_THRESHOLD - 1)
        signals.append(f"NO_CORROBORATION(capped\u2192{score})")

    return score


def compute_score(p1: dict, p2: dict, name_freq: int) -> tuple:
    """Return the donor-match score and the evidence behind it."""
    score, signals, has_geo, common_employers = _shared_evidence(p1, p2)

    points, evidence = _occupation_evidence(p1, p2)
    score += points
    signals.extend(evidence)

    points, evidence, hard_block = _middle_name_evidence(
        p1["middle"], p2["middle"]
    )
    signals.extend(evidence)
    if hard_block:
        return -999, signals
    score += points

    if name_freq <= 3:
        score += SCORE_RARE_NAME
        signals.append(f"rare({name_freq})(+{SCORE_RARE_NAME})")
    elif name_freq > 10:
        score += SCORE_COMMON_PENALTY
        signals.append(f"common({name_freq})({SCORE_COMMON_PENALTY})")

    if name_freq <= 2:
        score += SCORE_VERY_RARE_BONUS
        signals.append(f"very_rare(+{SCORE_VERY_RARE_BONUS})")

    no_employers = not p1.get("norm_employers", set()) and not p2.get(
        "norm_employers", set()
    )
    if not common_employers and has_geo and name_freq <= 3 and no_employers:
        score += SCORE_RETIRED_NO_EMP
        signals.append(f"retired_no_emp_rare_geo(+{SCORE_RETIRED_NO_EMP})")

    if not has_geo:
        score = _apply_no_geo_safety(
            score,
            signals,
            p1,
            name_freq,
            common_employers,
        )

    return score, signals


def _are_cross_group_candidates(norm1: str, norm2: str) -> bool:
    """True when two LAST|FIRST norms share the surname and the first names are a nickname pair, edit distance <= 1, or a distance-2 prefix truncation; blocked look-alike pairs never match."""
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
    """True for surname spelling variants (typo, squashed space/hyphen/dot, glued initial, truncation); the calling phase still demands strong geography before merging."""
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
