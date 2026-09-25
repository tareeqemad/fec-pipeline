"""The five pair-generation phases, in run order: within name groups, cross-group nicknames/typos, surname spelling variants, name-format variants (same token set), and maiden/married surname supersets."""

import re
from collections import defaultdict
from dataclasses import dataclass

from .constants import (
    MERGE_THRESHOLD,
    SCORE_CROSS_NAME_BONUS,
)
from .rules import identities_must_stay_separate
from .scoring import _are_cross_group_candidates, _is_surname_variant, compute_score

# one or more spaces/commas, to split a name into words
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


# score record pairs across two gated names in a bucket
def _score_bucket_pairs(context: MatchContext, buckets: dict, names_match, records_match, adjust) -> None:
    """Score each pair of records under two names of a bucket.

    names_match(a, b) gates a pair of normalized names; records_match(p1, p2)
    gates a pair of profiles before scoring; adjust(score, signals) returns the
    phase's final score and may add signals. Pairs are visited in sorted name
    order, so merges happen in the same order on every run.
    """
    for norms in buckets.values():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i, name_a in enumerate(norm_list):
            for name_b in norm_list[i + 1:]:
                if not names_match(name_a, name_b):
                    continue
                rids_a = context.name_groups[name_a]
                rids_b = context.name_groups[name_b]
                combined_freq = len(rids_a) + len(rids_b)
                for rid_a in rids_a:
                    for rid_b in rids_b:
                        p1, p2 = context.profiles[rid_a], context.profiles[rid_b]
                        if not records_match(p1, p2):
                            continue
                        score, signals = compute_score(p1, p2, combined_freq)
                        score = adjust(score, signals)
                        _merge_and_audit(
                            context, rid_a, rid_b, f"{name_a} ↔ {name_b}", score, signals,
                        )


# any pair of profiles
def _any_records(_p1: dict, _p2: dict) -> bool:
    return True


# same street, or the same full ZIP5
def _same_street_or_zip(p1: dict, p2: dict) -> bool:
    z1, z2 = p1["zip5"], p2["zip5"]
    return bool(p1["streets"] & p2["streets"]) or bool(z1 and len(z1) == 5 and z1 == z2)


# first-name variants get the bonus only with an anchor
def _cross_name_adjust(score: float, signals: list) -> float:
    if any(signal.startswith(("street(", "zip5=", "employer=")) for signal in signals):
        signals.append(f"cross_name(+{SCORE_CROSS_NAME_BONUS})")
        return score + SCORE_CROSS_NAME_BONUS
    signals.append("CROSS_NAME_NO_ANCHOR")
    return min(score, MERGE_THRESHOLD - 1)


# match first-name variants corroborated by street, ZIP, or employer
def _score_cross_groups(context: MatchContext) -> None:
    """Match first-name variants only when street, ZIP, or employer corroborates them."""
    _score_bucket_pairs(
        context, _norms_by_last_name(context.name_groups),
        _are_cross_group_candidates, _any_records, _cross_name_adjust,
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


# same street, or the same ZIP5 and a shared employer
def _same_street_or_zip_and_employer(p1: dict, p2: dict) -> bool:
    z1, z2 = p1["zip5"], p2["zip5"]
    same_zip = bool(z1 and len(z1) == 5 and z1 == z2)
    same_employer = bool(p1["norm_employers"] & p2["norm_employers"])
    return bool(p1["streets"] & p2["streets"]) or (same_zip and same_employer)


# two names whose surnames are typos of each other
def _surnames_vary(name_a: str, name_b: str) -> bool:
    return _is_surname_variant(name_a.split("|", 1)[0], name_b.split("|", 1)[0])


# tag a surname-variant pair without changing its score
def _surname_variant_adjust(score: float, signals: list) -> float:
    signals.append("surname_variant")
    return score


# match surname typos sharing a street or a ZIP/employer
def _score_surname_variants(context: MatchContext) -> None:
    """Match surname typos only when the records share a street, or a ZIP and employer."""
    _score_bucket_pairs(
        context, _norms_by_first_name(context.name_groups),
        _surnames_vary, _same_street_or_zip_and_employer, _surname_variant_adjust,
    )
