"""Phase 1 and 2 pair scoring: within name groups, then cross-group nicknames/typos."""

from collections import defaultdict

from fec.log import get_logger

from .constants import MERGE_THRESHOLD, SCORE_CROSS_NAME_BONUS, _is_blocked_merge
from .structures import UnionFind
from .scoring import compute_score, _are_cross_group_candidates

logger = get_logger(__name__)


def _merge_and_audit(
    p1: dict, p2: dict, rid_a: str, rid_b: str, label: str,
    score: int, signals: list,
    uf: UnionFind, audit_log: list, score_dist: dict,
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
    uf: UnionFind, audit_log: list, score_dist: dict,
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
    uf: UnionFind, audit_log: list, score_dist: dict,
    verbose: bool,
) -> tuple[int, int]:
    """Cross-group matching: nicknames + first-name typos sharing a surname. No geography pre-gate of its own -- compute_score decides, and the +10 cross-name bonus is withheld when the pair scored NO_CORROBORATION."""
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

                        no_corrob = any("NO_CORROBORATION" in s for s in signals)
                        if not no_corrob:
                            score += SCORE_CROSS_NAME_BONUS
                            signals.append(f"cross_name(+{SCORE_CROSS_NAME_BONUS})")

                        cross_pairs += 1
                        m, s = _merge_and_audit(
                            p1, p2, rid_a, rid_b,
                            f"{norm_list[i]} \u2194 {norm_list[j]}",
                            score, signals, uf, audit_log, score_dist,
                        )
                        cross_merge += m
                        cross_skip += s

    if verbose:
        logger.info(f"  Cross-group pairs scored: {cross_pairs:>5,}")
        logger.info(f"  Cross-group merged:       {cross_merge:>5,}")
        logger.info(f"  Cross-group skipped:      {cross_skip:>5,}")

    return cross_merge, cross_skip
