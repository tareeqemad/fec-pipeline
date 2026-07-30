"""Phases 2.5, 2.6 and 2.7: matching across surname variants, surname supersets, and name-format variants."""

import re
from collections import defaultdict

from fec.log import get_logger

from .constants import SCORE_CROSS_NAME_BONUS, _is_blocked_merge
from .structures import UnionFind
from .scoring import compute_score, _is_surname_variant

logger = get_logger(__name__)

_SUFFIX_TOKENS = frozenset({'JR', 'SR', 'II', 'III', 'IV', 'V'})
_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


def _score_surname_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> tuple[int, int]:
    """Match records whose surnames are spelling variants and first names equal; a merge additionally requires the same street or same ZIP5, so families are never fused on a near-miss surname."""
    first_to_norms = defaultdict(set)
    for nn in name_groups:
        if "|" in nn:
            last, first = nn.split("|", 1)
            if first and last:
                first_to_norms[first].add(nn)

    merged = 0
    skipped = 0
    for first, norms in first_to_norms.items():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            last_i = norm_list[i].split("|", 1)[0]
            for j in range(i + 1, len(norm_list)):
                last_j = norm_list[j].split("|", 1)[0]
                if not _is_surname_variant(last_i, last_j):
                    continue

                rids_a = name_groups[norm_list[i]]
                rids_b = name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = profiles[ra], profiles[rb]

                        z1, z2 = p1["zip5"], p2["zip5"]
                        strong_geo = bool(p1["streets"] & p2["streets"]) or (
                            z1 and len(z1) == 5 and z1 == z2
                        )
                        if not strong_geo:
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        signals.append("surname_variant")
                        score_dist[(score // 10) * 10] += 1

                        did_merge = False
                        if score >= threshold:
                            if _is_blocked_merge(p1["name"], p2["name"]):
                                skipped += 1
                                signals.append("BLOCKED(do_not_merge)")
                            elif uf.union(ra, rb):
                                merged += 1
                                did_merge = True
                        else:
                            skipped += 1

                        audit_log.append({
                            "rid_a": ra,
                            "rid_b": rb,
                            "norm_name": f"{norm_list[i]} ↔ {norm_list[j]}",
                            "score": score,
                            "merged": did_merge,
                            "signals": "; ".join(signals),
                        })

    if verbose:
        logger.info("\n  -- Surname-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _score_surname_superset_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> tuple[int, int]:
    """Merge a maiden/short name whose token set is a proper subset of the married/compound form; gated on a shared first-name token, non-suffix extra tokens, and the same exact street."""
    key_of: dict = {}
    first_of: dict = {}
    for nn in name_groups:
        if '|' not in nn:
            continue
        last, _, first = nn.partition('|')
        toks = frozenset(t for t in _TOKEN_SPLIT_RE.split(f'{last} {first}') if len(t) >= 2)
        if len(toks) >= 2:
            key_of[nn] = toks
            first_of[nn] = frozenset(t for t in _TOKEN_SPLIT_RE.split(first) if len(t) >= 2)

    tok_index: dict = defaultdict(set)
    for nn, toks in key_of.items():
        for t in toks:
            tok_index[t].add(nn)

    merged = 0
    skipped = 0
    seen_pairs = set()
    for nn1, k1 in key_of.items():
        # norms whose token set contains ALL of k1's tokens = supersets of nn1
        cand = None
        for t in k1:
            cand = set(tok_index[t]) if cand is None else (cand & tok_index[t])
        if not cand:
            continue
        for nn2 in cand:
            if nn2 == nn1:
                continue
            k2 = key_of[nn2]
            if not (k1 < k2):                      # require PROPER subset
                continue
            extra = k2 - k1
            if len(extra) > 2 or extra <= _SUFFIX_TOKENS:  # suffix-only diff = father/son
                continue
            # the shorter name's first-name token must appear in the longer name
            if not (first_of.get(nn1, frozenset()) & k2):
                continue
            pair = (nn1, nn2)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            combined_freq = len(name_groups[nn1]) + len(name_groups[nn2])
            for ra in name_groups[nn1]:
                for rb in name_groups[nn2]:
                    p1, p2 = profiles[ra], profiles[rb]
                    if not (p1['streets'] & p2['streets']):   # SAME exact street only
                        continue
                    score, signals = compute_score(p1, p2, combined_freq)
                    if not any(('MIDDLE_CONFLICT' in s) or ('HARD_BLOCK' in s) for s in signals):
                        score += SCORE_CROSS_NAME_BONUS
                    signals.append("surname_superset")
                    score_dist[(score // 10) * 10] += 1

                    did_merge = False
                    if score >= threshold:
                        if _is_blocked_merge(p1["name"], p2["name"]):
                            skipped += 1
                            signals.append("BLOCKED(do_not_merge)")
                        elif uf.union(ra, rb):
                            merged += 1
                            did_merge = True
                    else:
                        skipped += 1

                    audit_log.append({
                        "rid_a": ra, "rid_b": rb,
                        "norm_name": f"{nn1} ⊂ {nn2}",
                        "score": score, "merged": did_merge,
                        "signals": "; ".join(signals),
                    })

    if verbose:
        logger.info("\n  -- Maiden/married (surname-superset) matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _name_token_key(norm_name: str) -> frozenset:
    """Order-independent set of a name's tokens of length >= 2; suffixes (JR/SR/II) are kept so a father/son pair never collapses."""
    last, _, first = norm_name.partition("|")
    return frozenset(t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2)


def _score_name_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> tuple[int, int]:
    """Match the same person written in a different name format, bucketed by token set and gated on same street or ZIP5; middle-name conflicts stay blocked/penalised."""
    token_to_norms = defaultdict(set)
    for nn in name_groups:
        if "|" not in nn:
            continue
        key = _name_token_key(nn)
        if len(key) >= 2:
            token_to_norms[key].add(nn)

    merged = 0
    skipped = 0
    for key, norms in token_to_norms.items():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            for j in range(i + 1, len(norm_list)):
                rids_a = name_groups[norm_list[i]]
                rids_b = name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = profiles[ra], profiles[rb]
                        z1, z2 = p1["zip5"], p2["zip5"]
                        strong_geo = bool(p1["streets"] & p2["streets"]) or (
                            z1 and len(z1) == 5 and z1 == z2
                        )
                        if not strong_geo:
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        # token-set match earns the cross-name credit only without a
                        # middle conflict, so a same-street father/son never tips over
                        if not any(('MIDDLE_CONFLICT' in s) or ('HARD_BLOCK' in s) for s in signals):
                            score += SCORE_CROSS_NAME_BONUS
                        signals.append("name_format_variant")
                        score_dist[(score // 10) * 10] += 1

                        did_merge = False
                        if score >= threshold:
                            if _is_blocked_merge(p1["name"], p2["name"]):
                                skipped += 1
                                signals.append("BLOCKED(do_not_merge)")
                            elif uf.union(ra, rb):
                                merged += 1
                                did_merge = True
                        else:
                            skipped += 1

                        audit_log.append({
                            "rid_a": ra,
                            "rid_b": rb,
                            "norm_name": f"{norm_list[i]} ↔ {norm_list[j]}",
                            "score": score,
                            "merged": did_merge,
                            "signals": "; ".join(signals),
                        })

    if verbose:
        logger.info("\n  -- Name-format-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped
