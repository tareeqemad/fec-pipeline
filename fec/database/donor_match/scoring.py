"""Scoring engine: compute merge confidence between donor record pairs."""

from fec.cleaning._helpers import levenshtein

from .constants import (
    NICKNAME_MAP, _BLOCKED_FIRST_PAIRS, MERGE_THRESHOLD,
    SCORE_SAME_STREET, SCORE_SAME_EMPLOYER, SCORE_SAME_STATE,
    SCORE_SAME_CITY, SCORE_SAME_ZIP5, SCORE_SAME_ZIP3,
    SCORE_MIDDLE_MATCH, SCORE_MIDDLE_PARTIAL, SCORE_MIDDLE_CONFLICT,
    SCORE_RARE_NAME, SCORE_VERY_RARE_BONUS, SCORE_COMMON_PENALTY,
    SCORE_SAME_OCC, SCORE_OCC_RETIRED,
    XSTATE_LAST_RARE_MAX, XSTATE_FIRST_RARE_MAX,
)

# bonus when both sides lack a real employer (retired-like) but share a rare
# name and geography
_SCORE_RETIRED_NO_EMP = 25


def compute_score(p1: dict, p2: dict, name_freq: int) -> tuple:
    """Score merge confidence for a pair, returning (score, signals); at least one geographic signal is required to merge -- employer or name rarity alone is never enough."""
    score = 0
    signals = []
    has_geo = False
    has_employer = False

    if p1["streets"] and p2["streets"]:
        common = p1["streets"] & p2["streets"]
        if common:
            score += SCORE_SAME_STREET
            signals.append(f"street(+{SCORE_SAME_STREET})")
            has_geo = True

    # employer match (real companies only)
    if p1["norm_employers"] and p2["norm_employers"]:
        common = p1["norm_employers"] & p2["norm_employers"]
        if common:
            score += SCORE_SAME_EMPLOYER
            signals.append(f"employer={list(common)[0][:25]}(+{SCORE_SAME_EMPLOYER})")
            has_employer = True

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

    # occupation corroboration; for a very rare name a consistent category also
    # unlocks the cross-state path below, while occ_conflict blocks it
    c1, c2 = p1.get("occ_categories", set()), p2.get("occ_categories", set())
    r1, r2 = p1.get("retired", False), p2.get("retired", False)
    occ_conflict = bool(c1 and c2 and not (c1 & c2))   # two DIFFERENT real careers
    occ_compatible = False
    if c1 and c2 and (c1 & c2):
        score += SCORE_SAME_OCC
        signals.append(f"occ={sorted(c1 & c2)[0][:18]}(+{SCORE_SAME_OCC})")
        occ_compatible = True
    elif (c1 and r2) or (c2 and r1):
        score += SCORE_OCC_RETIRED
        signals.append(f"occ_retired_transition(+{SCORE_OCC_RETIRED})")
        occ_compatible = True
    elif r1 and r2:
        occ_compatible = True                           # both retired: consistent, no points

    m1, m2 = p1["middle"], p2["middle"]
    if m1 and m2:
        if m1 == m2:
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_ok={m1}(+{SCORE_MIDDLE_MATCH})")
        elif len(m1) == 1 and m2[0] == m1:
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_ok={m1}/{m2}(+{SCORE_MIDDLE_MATCH})")
        elif len(m2) == 1 and m1[0] == m2:
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_ok={m1}/{m2}(+{SCORE_MIDDLE_MATCH})")
        elif len(m1) == 1 and len(m2) == 1:
            score += SCORE_MIDDLE_CONFLICT
            signals.append(f"MIDDLE_CONFLICT={m1}\u2260{m2}({SCORE_MIDDLE_CONFLICT})")
        elif _is_typo(m1, m2):
            score += SCORE_MIDDLE_MATCH
            signals.append(f"middle_typo={m1}/{m2}(+{SCORE_MIDDLE_MATCH})")
        else:
            score = -999
            signals.append(f"HARD_BLOCK={m1}\u2260{m2} (different people)")
            return score, signals
    elif m1 or m2:
        score += SCORE_MIDDLE_PARTIAL
        signals.append(f"middle_partial(+{SCORE_MIDDLE_PARTIAL})")

    if name_freq <= 3:
        score += SCORE_RARE_NAME
        signals.append(f"rare({name_freq})(+{SCORE_RARE_NAME})")
    elif name_freq > 10:
        score += SCORE_COMMON_PENALTY
        signals.append(f"common({name_freq})({SCORE_COMMON_PENALTY})")

    if name_freq <= 2:
        score += SCORE_VERY_RARE_BONUS
        signals.append(f"very_rare(+{SCORE_VERY_RARE_BONUS})")

    # RETIRED/no-employer + rare name + same geo
    if (not has_employer and has_geo and name_freq <= 3
            and not p1.get("norm_employers", set())
            and not p2.get("norm_employers", set())):
        score += _SCORE_RETIRED_NO_EMP
        signals.append(f"retired_no_emp_rare_geo(+{_SCORE_RETIRED_NO_EMP})")

    is_eponymous = False
    if has_employer and not has_geo:
        name_str = p1.get("name", "")
        last1 = name_str.split(",")[0].strip().upper() if "," in name_str else ""
        if last1 and len(last1) >= 3:
            for emp in (p1.get("norm_employers", set()) & p2.get("norm_employers", set())):
                if last1 in emp.upper():
                    is_eponymous = True
                    break

    # SAFETY: require geographic corroboration
    if not has_geo:
        if has_employer:
            if name_freq <= 3:
                score += 10
                signals.append(f"rare_employer_ok(freq={name_freq},+10)")
            elif is_eponymous and name_freq <= 10:
                signals.append(f"eponymous_employer(freq={name_freq})")
            elif name_freq <= 10:
                score = min(score, MERGE_THRESHOLD - 1)
                signals.append(f"EMPLOYER_ONLY_NO_GEO(capped\u2192{score},freq={name_freq})")
            else:
                score = min(score, MERGE_THRESHOLD - 20)
                signals.append(f"EMPLOYER_ONLY_COMMON(capped\u2192{score},freq={name_freq})")
        else:
            # the only merge path with no geography: very rare name (freq<=2) with a
            # consistent occupation/life-status and no career conflict -- one person
            # who moved or keeps two homes; common names are never fused here
            distinctive = (
                p1.get("last_freq", 999) <= XSTATE_LAST_RARE_MAX
                or (p1.get("first_freq", 999) <= XSTATE_FIRST_RARE_MAX
                    and p2.get("first_freq", 999) <= XSTATE_FIRST_RARE_MAX)
            )
            if name_freq <= 2 and occ_compatible and not occ_conflict and distinctive:
                score = max(score, MERGE_THRESHOLD)
                signals.append(f"RARE_OCC_CROSS_STATE(allow\u2192{score})")
            elif name_freq <= 2:
                score = min(score, MERGE_THRESHOLD - 1)
                signals.append(f"NO_CORROBORATION_RARE(capped\u2192{score})")
            else:
                score = min(score, MERGE_THRESHOLD - 1)
                signals.append(f"NO_CORROBORATION(capped\u2192{score})")

    return score, signals


def _is_typo(a: str, b: str) -> bool:
    """True if the two strings differ by at most one edit."""
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(c1 != c2 for c1, c2 in zip(a, b)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    diffs = 0
    si = 0
    for li in range(len(long)):
        if si < len(short) and short[si] == long[li]:
            si += 1
        else:
            diffs += 1
    return diffs <= 1


def _canonical_first(name: str) -> str:
    """Map first name to canonical form for cross-group candidate selection."""
    return NICKNAME_MAP.get(name, name)


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

    if _canonical_first(first1) == _canonical_first(first2):
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
