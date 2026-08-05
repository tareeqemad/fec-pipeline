"""The five pair-generation phases, in run order: within name groups, cross-group nicknames/typos, surname spelling variants, name-format variants (same token set), and maiden/married surname supersets."""

import re
from collections import defaultdict

from fec.log import get_logger

from .constants import MERGE_THRESHOLD, SCORE_CROSS_NAME_BONUS, _is_blocked_merge
from .scoring import compute_score, _are_cross_group_candidates, _is_surname_variant

logger = get_logger(__name__)

_SUFFIX_TOKENS = frozenset({'JR', 'SR', 'II', 'III', 'IV', 'V'})
_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


def _merge_and_audit(
    p1: dict, p2: dict, rid_a: str, rid_b: str, label: str,
    score: int, signals: list,
    uf, audit_log: list, score_dist: dict,
) -> tuple[bool, bool]:
    """Bucket the score, union the pair at/above MERGE_THRESHOLD unless blocklisted, append the audit row; returns (merged, skipped)."""
    score_dist[(score // 10) * 10] += 1
    merged = False
    skipped = False
    if score >= MERGE_THRESHOLD:
        if _is_blocked_merge(p1["name"], p2["name"]):
            skipped = True
            signals.append("BLOCKED(do_not_merge)")
        elif uf.union(rid_a, rid_b):
            merged = True
    else:
        skipped = True
    audit_log.append({
        "rid_a": rid_a,
        "rid_b": rid_b,
        "norm_name": label,
        "score": score,
        "merged": merged,
        "signals": "; ".join(signals),
    })
    return merged, skipped


def _score_within_groups(
    name_groups: dict, profiles: dict,
    uf, audit_log: list, score_dist: dict,
    verbose: bool,
) -> tuple[int, int]:
    """Score all candidate pairs within each name group."""
    merge_count = 0
    skip_count = 0

    if verbose:
        logger.info(f"\n  -- Scoring pairs (threshold={MERGE_THRESHOLD}) --")

    for norm_name, rids in name_groups.items():
        if len(rids) < 2:
            continue

        name_freq = len(rids)

        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                p1 = profiles[rids[i]]
                p2 = profiles[rids[j]]

                score, signals = compute_score(p1, p2, name_freq)

                m, s = _merge_and_audit(
                    p1, p2, rids[i], rids[j], norm_name,
                    score, signals, uf, audit_log, score_dist,
                )
                merge_count += m
                skip_count += s

    return merge_count, skip_count


def _score_cross_groups(
    name_groups: dict, profiles: dict,
    uf, audit_log: list, score_dist: dict,
    verbose: bool,
) -> tuple[int, int]:
    """Match first-name variants only when street, ZIP, or employer corroborates them."""
    if verbose:
        logger.info("\n  -- Cross-group matching (nicknames + typos) --")

    last_to_norms = defaultdict(set)
    for nn in name_groups:
        if "|" in nn:
            last = nn.split("|", 1)[0]
            last_to_norms[last].add(nn)

    cross_merge = 0
    cross_skip = 0
    cross_pairs = 0

    for last, norms in last_to_norms.items():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            for j in range(i + 1, len(norm_list)):
                if not _are_cross_group_candidates(norm_list[i], norm_list[j]):
                    continue

                rids_a = name_groups[norm_list[i]]
                rids_b = name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for rid_a in rids_a:
                    for rid_b in rids_b:
                        p1 = profiles[rid_a]
                        p2 = profiles[rid_b]

                        score, signals = compute_score(p1, p2, combined_freq)
                        has_anchor = any(
                            signal.startswith(("street(", "zip5=", "employer="))
                            for signal in signals
                        )
                        if has_anchor:
                            score += SCORE_CROSS_NAME_BONUS
                            signals.append(f"cross_name(+{SCORE_CROSS_NAME_BONUS})")
                        else:
                            score = min(score, MERGE_THRESHOLD - 1)
                            signals.append("CROSS_NAME_NO_ANCHOR")

                        cross_pairs += 1
                        m, s = _merge_and_audit(
                            p1, p2, rid_a, rid_b,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score, signals, uf, audit_log, score_dist,
                        )
                        cross_merge += m
                        cross_skip += s

    if verbose:
        logger.info(f"  Cross-group pairs scored: {cross_pairs:>5,}")
        logger.info(f"  Cross-group merged:       {cross_merge:>5,}")
        logger.info(f"  Cross-group skipped:      {cross_skip:>5,}")

    return cross_merge, cross_skip


def _score_surname_variants(
    name_groups: dict, profiles: dict, uf,
    audit_log: list, score_dist: dict, verbose: bool,
) -> tuple[int, int]:
    """Match surname typos only when the records share a street, or a ZIP and employer."""
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

                        same_street = bool(p1["streets"] & p2["streets"])
                        same_employer = bool(
                            p1["norm_employers"] & p2["norm_employers"]
                        )
                        z1, z2 = p1["zip5"], p2["zip5"]
                        same_zip = bool(z1 and len(z1) == 5 and z1 == z2)
                        if not (same_street or (same_zip and same_employer)):
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        signals.append("surname_variant")
                        m, s = _merge_and_audit(
                            p1, p2, ra, rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score, signals, uf, audit_log, score_dist,
                        )
                        merged += m
                        skipped += s

    if verbose:
        logger.info("\n  -- Surname-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _name_token_key(norm_name: str) -> frozenset:
    """Order-independent set of a name's tokens of length >= 2; suffixes (JR/SR/II) are kept so a father/son pair never collapses."""
    last, _, first = norm_name.partition("|")
    return frozenset(t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2)


def _score_name_variants(
    name_groups: dict, profiles: dict, uf,
    audit_log: list, score_dist: dict, verbose: bool,
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
                        m, s = _merge_and_audit(
                            p1, p2, ra, rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score, signals, uf, audit_log, score_dist,
                        )
                        merged += m
                        skipped += s

    if verbose:
        logger.info("\n  -- Name-format-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _score_surname_superset_variants(
    name_groups: dict, profiles: dict, uf,
    audit_log: list, score_dist: dict, verbose: bool,
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
    for nn1, k1 in key_of.items():
        # norms whose token set contains ALL of k1's tokens = supersets of nn1
        cand = None
        for t in k1:
            cand = set(tok_index[t]) if cand is None else (cand & tok_index[t])
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
            if not (first_of[nn1] & k2):
                continue

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
                    m, s = _merge_and_audit(
                        p1, p2, ra, rb,
                        f"{nn1} ⊂ {nn2}",
                        score, signals, uf, audit_log, score_dist,
                    )
                    merged += m
                    skipped += s

    if verbose:
        logger.info("\n  -- Maiden/married (surname-superset) matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped
