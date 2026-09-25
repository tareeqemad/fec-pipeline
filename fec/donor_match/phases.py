"""The five pair-generation phases, in run order: within name groups, cross-group nicknames/typos, surname spelling variants, name-format variants (same token set), and maiden/married surname supersets."""

import re
from collections import defaultdict
from dataclasses import dataclass

from .constants import (
    MERGE_THRESHOLD,
    SCORE_CROSS_NAME_BONUS,
)
from .rules import identities_must_stay_separate
from .scoring import compute_score, _are_cross_group_candidates, _is_surname_variant

_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


@dataclass
class MatchContext:
    name_groups: dict
    profiles: dict
    union_find: object
    audit_log: list
    component_suffixes: dict


# check generational-suffix conflicts before allowing a merge
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


# union two records' components, merging their suffix sets
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


# score and merge one candidate pair
def _merge_and_audit(
    context: MatchContext,
    rid_a: str,
    rid_b: str,
    label: str,
    score: int,
    signals: list,
) -> None:
    """Score and merge one candidate pair."""
    p1 = context.profiles[rid_a]
    p2 = context.profiles[rid_b]
    merged = False
    if score >= MERGE_THRESHOLD:
        suffix_allowed, suffix_signal = _suffixes_allow_merge(context, rid_a, rid_b)
        if not suffix_allowed:
            signals.append(suffix_signal)
        elif identities_must_stay_separate(p1, p2):
            signals.append("SEPARATED(curated_rule)")
        elif _join_components(context, rid_a, rid_b):
            merged = True
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


# score all candidate pairs within each name group
def _score_within_groups(
    context: MatchContext,
) -> None:
    """Score all candidate pairs within each name group."""
    for norm_name, rids in context.name_groups.items():
        if len(rids) < 2:
            continue

        name_freq = len(rids)

        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                p1 = context.profiles[rids[i]]
                p2 = context.profiles[rids[j]]

                score, signals = compute_score(p1, p2, name_freq)

                _merge_and_audit(
                    context,
                    rids[i],
                    rids[j],
                    norm_name,
                    score,
                    signals,
                )


# group normalized names by shared last name
def _norms_by_last_name(name_groups: dict) -> dict:
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" in norm_name:
            grouped[norm_name.split("|", 1)[0]].add(norm_name)
    return grouped


# match first-name variants corroborated by street, ZIP, or employer
def _score_cross_groups(
    context: MatchContext,
) -> None:
    """Match first-name variants only when street, ZIP, or employer corroborates them."""
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

                        _merge_and_audit(
                            context,
                            rid_a,
                            rid_b,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )


# group normalized names by shared first name
def _norms_by_first_name(name_groups: dict) -> dict:
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, first = norm_name.split("|", 1)
        if first and last:
            grouped[first].add(norm_name)
    return grouped


# match surname typos sharing a street or a ZIP/employer
def _score_surname_variants(
    context: MatchContext,
) -> None:
    """Match surname typos only when the records share a street, or a ZIP and employer."""
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
                        _merge_and_audit(
                            context,
                            ra,
                            rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )


