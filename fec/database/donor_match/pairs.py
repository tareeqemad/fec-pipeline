"""Phase 1 and 2 pair scoring: within name groups, then cross-group nicknames/typos."""

from collections import defaultdict

from fec.log import get_logger

from .constants import SCORE_CROSS_NAME_BONUS, _is_blocked_merge
from .structures import UnionFind
from .scoring import compute_score, _are_cross_group_candidates

logger = get_logger(__name__)


def _score_within_groups(
    name_groups: dict, norm_name_counts: dict, profiles: dict,
    uf: UnionFind, audit_log: list, score_dist: dict,
    threshold: int, verbose: bool,
) -> tuple[int, int]:
    """Score all candidate pairs within each name group."""
    merge_count = 0
    skip_count = 0

    if verbose:
        logger.info(f"\n  -- Scoring pairs (threshold={threshold}) --")

    for norm_name, rids in name_groups.items():
        if len(rids) < 2:
            continue

        name_freq = norm_name_counts[norm_name]

        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                p1 = profiles[rids[i]]
                p2 = profiles[rids[j]]

                score, signals = compute_score(p1, p2, name_freq)

                bucket = (score // 10) * 10
                score_dist[bucket] += 1

                merged = False
                if score >= threshold:
                    if _is_blocked_merge(p1["name"], p2["name"]):
                        skip_count += 1
                        signals.append("BLOCKED(do_not_merge)")
                    elif uf.union(rids[i], rids[j]):
                        merge_count += 1
                        merged = True
                else:
                    skip_count += 1

                audit_log.append({
                    "rid_a": rids[i],
                    "rid_b": rids[j],
                    "norm_name": norm_name,
                    "score": score,
                    "merged": merged,
                    "signals": "; ".join(signals),
                })

    return merge_count, skip_count


def _score_cross_groups(
    name_groups: dict, profiles: dict,
    uf: UnionFind, audit_log: list, score_dist: dict,
    threshold: int, verbose: bool,
) -> tuple[int, int]:
    """Cross-group matching: nicknames + first-name typos. Requires street match."""
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
                        bucket = (score // 10) * 10
                        score_dist[bucket] += 1

                        merged = False
                        if score >= threshold:
                            if _is_blocked_merge(p1["name"], p2["name"]):
                                cross_skip += 1
                                signals.append("BLOCKED(do_not_merge)")
                            elif uf.union(rid_a, rid_b):
                                cross_merge += 1
                                merged = True
                        else:
                            cross_skip += 1

                        audit_log.append({
                            "rid_a": rid_a,
                            "rid_b": rid_b,
                            "norm_name": f"{norm_list[i]} \u2194 {norm_list[j]}",
                            "score": score,
                            "merged": merged,
                            "signals": "; ".join(signals),
                        })

    if verbose:
        logger.info(f"  Cross-group pairs scored: {cross_pairs:>5,}")
        logger.info(f"  Cross-group merged:       {cross_merge:>5,}")
        logger.info(f"  Cross-group skipped:      {cross_skip:>5,}")

    return cross_merge, cross_skip
