"""The five pair-generation phases, in run order: within name groups, cross-group nicknames/typos, surname spelling variants, name-format variants (same token set), and maiden/married surname supersets."""

import re
from collections import defaultdict
from dataclasses import dataclass

from fec.log import get_logger

from .constants import (
    MERGE_THRESHOLD,
    SCORE_CROSS_NAME_BONUS,
)
from .rules import identities_must_stay_separate
from .scoring import compute_score, _are_cross_group_candidates, _is_surname_variant

logger = get_logger(__name__)

_SUFFIX_TOKENS = frozenset({"JR", "SR", "II", "III", "IV", "V"})
_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


@dataclass
class MatchContext:
    name_groups: dict
    profiles: dict
    union_find: object
    audit_log: list
    score_dist: dict
    verbose: bool
    component_suffixes: dict


def _suffixes_allow_merge(
    context: MatchContext, rid_a: str, rid_b: str
) -> tuple[bool, str]:
    root_a = context.union_find.find(rid_a)
    root_b = context.union_find.find(rid_b)
    suffixes_a = context.component_suffixes[root_a]
    suffixes_b = context.component_suffixes[root_b]
    if len(suffixes_a | suffixes_b) > 1:
        return False, "SUFFIX_CONFLICT"
    if bool(suffixes_a) != bool(suffixes_b):
        p1 = context.profiles[rid_a]
        p2 = context.profiles[rid_b]
        if not (p1["streets"] & p2["streets"]):
            return False, "SUFFIX_MISSING_NO_STREET"
    return True, ""


def _join_components(context: MatchContext, rid_a: str, rid_b: str) -> bool:
    root_a = context.union_find.find(rid_a)
    root_b = context.union_find.find(rid_b)
    suffixes = context.component_suffixes[root_a] | context.component_suffixes[root_b]
    if not context.union_find.union(rid_a, rid_b):
        return False
    new_root = context.union_find.find(rid_a)
    context.component_suffixes.pop(root_a, None)
    context.component_suffixes.pop(root_b, None)
    context.component_suffixes[new_root] = suffixes
    return True


def _merge_and_audit(
    context: MatchContext,
    rid_a: str,
    rid_b: str,
    label: str,
    score: int,
    signals: list,
) -> tuple[bool, bool]:
    """Score and merge one candidate pair."""
    p1 = context.profiles[rid_a]
    p2 = context.profiles[rid_b]
    context.score_dist[(score // 10) * 10] += 1
    merged = False
    skipped = False
    if score >= MERGE_THRESHOLD:
        suffix_allowed, suffix_signal = _suffixes_allow_merge(context, rid_a, rid_b)
        if not suffix_allowed:
            skipped = True
            signals.append(suffix_signal)
        elif identities_must_stay_separate(p1, p2):
            skipped = True
            signals.append("SEPARATED(curated_rule)")
        elif _join_components(context, rid_a, rid_b):
            merged = True
    else:
        skipped = True
    context.audit_log.append(
        {
            "rid_a": rid_a,
            "rid_b": rid_b,
            "norm_name": label,
            "score": score,
            "merged": merged,
            "signals": "; ".join(signals),
        }
    )
    return merged, skipped


def _score_within_groups(
    context: MatchContext,
) -> tuple[int, int]:
    """Score all candidate pairs within each name group."""
    merge_count = 0
    skip_count = 0

    if context.verbose:
        logger.info(f"\n  -- Scoring pairs (threshold={MERGE_THRESHOLD}) --")

    for norm_name, rids in context.name_groups.items():
        if len(rids) < 2:
            continue

        name_freq = len(rids)

        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                p1 = context.profiles[rids[i]]
                p2 = context.profiles[rids[j]]

                score, signals = compute_score(p1, p2, name_freq)

                m, s = _merge_and_audit(
                    context,
                    rids[i],
                    rids[j],
                    norm_name,
                    score,
                    signals,
                )
                merge_count += m
                skip_count += s

    return merge_count, skip_count


def _norms_by_last_name(name_groups: dict) -> dict:
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" in norm_name:
            grouped[norm_name.split("|", 1)[0]].add(norm_name)
    return grouped


def _score_cross_groups(
    context: MatchContext,
) -> tuple[int, int]:
    """Match first-name variants only when street, ZIP, or employer corroborates them."""
    if context.verbose:
        logger.info("\n  -- Cross-group matching (nicknames + typos) --")

    cross_merge = 0
    cross_skip = 0
    cross_pairs = 0

    for norms in _norms_by_last_name(context.name_groups).values():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            candidates = (
                j
                for j in range(i + 1, len(norm_list))
                if _are_cross_group_candidates(norm_list[i], norm_list[j])
            )
            for j in candidates:
                rids_a = context.name_groups[norm_list[i]]
                rids_b = context.name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for rid_a in rids_a:
                    for rid_b in rids_b:
                        p1 = context.profiles[rid_a]
                        p2 = context.profiles[rid_b]

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
                            context,
                            rid_a,
                            rid_b,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )
                        cross_merge += m
                        cross_skip += s

    if context.verbose:
        logger.info(f"  Cross-group pairs scored: {cross_pairs:>5,}")
        logger.info(f"  Cross-group merged:       {cross_merge:>5,}")
        logger.info(f"  Cross-group skipped:      {cross_skip:>5,}")

    return cross_merge, cross_skip


def _norms_by_first_name(name_groups: dict) -> dict:
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, first = norm_name.split("|", 1)
        if first and last:
            grouped[first].add(norm_name)
    return grouped


def _score_surname_variants(
    context: MatchContext,
) -> tuple[int, int]:
    """Match surname typos only when the records share a street, or a ZIP and employer."""
    merged = 0
    skipped = 0
    for norms in _norms_by_first_name(context.name_groups).values():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            last_i = norm_list[i].split("|", 1)[0]
            for j in range(i + 1, len(norm_list)):
                last_j = norm_list[j].split("|", 1)[0]
                if not _is_surname_variant(last_i, last_j):
                    continue

                rids_a = context.name_groups[norm_list[i]]
                rids_b = context.name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = context.profiles[ra], context.profiles[rb]

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
                            context,
                            ra,
                            rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )
                        merged += m
                        skipped += s

    if context.verbose:
        logger.info("\n  -- Surname-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _name_token_key(norm_name: str) -> frozenset:
    """Return the order-independent significant name tokens."""
    last, _, first = norm_name.partition("|")
    return frozenset(t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2)


def _norms_by_name_tokens(name_groups: dict) -> dict:
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        key = _name_token_key(norm_name)
        if len(key) >= 2:
            grouped[key].add(norm_name)
    return grouped


def _score_name_variants(
    context: MatchContext,
) -> tuple[int, int]:
    """Match the same person written in a different name format, bucketed by token set and gated on same street or ZIP5; middle-name conflicts stay blocked/penalised."""
    merged = 0
    skipped = 0
    for norms in _norms_by_name_tokens(context.name_groups).values():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            for j in range(i + 1, len(norm_list)):
                rids_a = context.name_groups[norm_list[i]]
                rids_b = context.name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = context.profiles[ra], context.profiles[rb]
                        z1, z2 = p1["zip5"], p2["zip5"]
                        strong_geo = bool(p1["streets"] & p2["streets"]) or (
                            z1 and len(z1) == 5 and z1 == z2
                        )
                        if not strong_geo:
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        # token-set match earns the cross-name credit only without a
                        # middle conflict, so a same-street father/son never tips over
                        if not any(
                            ("MIDDLE_CONFLICT" in s) or ("HARD_BLOCK" in s)
                            for s in signals
                        ):
                            score += SCORE_CROSS_NAME_BONUS
                        signals.append("name_format_variant")
                        m, s = _merge_and_audit(
                            context,
                            ra,
                            rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )
                        merged += m
                        skipped += s

    if context.verbose:
        logger.info("\n  -- Name-format-variant matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _superset_name_index(name_groups: dict) -> tuple[dict, dict, dict]:
    key_of: dict = {}
    first_of: dict = {}
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, _, first = norm_name.partition("|")
        toks = frozenset(
            t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2
        )
        if len(toks) >= 2:
            key_of[norm_name] = toks
            first_of[norm_name] = frozenset(
                t for t in _TOKEN_SPLIT_RE.split(first) if len(t) >= 2
            )

    tok_index: dict = defaultdict(set)
    for norm_name, tokens in key_of.items():
        for token in tokens:
            tok_index[token].add(norm_name)
    return key_of, first_of, tok_index


def _superset_candidates(tokens: frozenset, token_index: dict) -> set:
    candidates = None
    for token in tokens:
        candidates = (
            set(token_index[token])
            if candidates is None
            else candidates & token_index[token]
        )
    return candidates


def _is_valid_superset(
    shorter_name: str,
    longer_name: str,
    shorter_tokens: frozenset,
    key_of: dict,
    first_of: dict,
) -> bool:
    if longer_name == shorter_name:
        return False
    longer_tokens = key_of[longer_name]
    if not shorter_tokens < longer_tokens:
        return False
    extra = longer_tokens - shorter_tokens
    if len(extra) > 2 or extra <= _SUFFIX_TOKENS:
        return False
    return bool(first_of[shorter_name] & longer_tokens)


def _score_surname_superset_variants(
    context: MatchContext,
) -> tuple[int, int]:
    """Match short and compound surnames only on the same exact street."""
    key_of, first_of, tok_index = _superset_name_index(context.name_groups)

    merged = 0
    skipped = 0
    for nn1, k1 in key_of.items():
        for nn2 in _superset_candidates(k1, tok_index):
            if not _is_valid_superset(nn1, nn2, k1, key_of, first_of):
                continue

            combined_freq = (
                len(context.name_groups[nn1]) + len(context.name_groups[nn2])
            )
            for ra in context.name_groups[nn1]:
                for rb in context.name_groups[nn2]:
                    p1, p2 = context.profiles[ra], context.profiles[rb]
                    if not (p1["streets"] & p2["streets"]):  # SAME exact street only
                        continue
                    score, signals = compute_score(p1, p2, combined_freq)
                    if not any(
                        ("MIDDLE_CONFLICT" in s) or ("HARD_BLOCK" in s) for s in signals
                    ):
                        score += SCORE_CROSS_NAME_BONUS
                    signals.append("surname_superset")
                    m, s = _merge_and_audit(
                        context,
                        ra,
                        rb,
                        f"{nn1} ⊂ {nn2}",
                        score,
                        signals,
                    )
                    merged += m
                    skipped += s

    if context.verbose:
        logger.info("\n  -- Maiden/married (surname-superset) matching --")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped
