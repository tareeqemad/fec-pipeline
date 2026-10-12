"""The pair-generation match phases, in order."""

import re
from collections import defaultdict
from dataclasses import dataclass

from fec.donor_match.constants import MERGE_THRESHOLD, SCORE_CROSS_NAME_BONUS
from fec.donor_match.rules import identities_must_stay_separate
from fec.donor_match.scoring import (
    are_cross_group_candidates,
    is_surname_variant,
    compute_score,
)

# spaces or commas between name words
TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


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


# union two components and their suffix sets
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


# merge one scored pair and audit it
def merge_and_audit(
    context: MatchContext,
    rid_a: str,
    rid_b: str,
    label: str,
    score: int,
    signals: list,
) -> None:
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


# score all pairs within each name group
def score_within_groups(
    context: MatchContext,
) -> None:
    for norm_name, rids in context.name_groups.items():
        if len(rids) < 2:
            continue

        name_freq = len(rids)

        for i in range(len(rids)):
            for j in range(i + 1, len(rids)):
                p1 = context.profiles[rids[i]]
                p2 = context.profiles[rids[j]]

                score, signals = compute_score(p1, p2, name_freq)

                merge_and_audit(
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


# score bucket pairs in sorted, repeatable order
def score_bucket_pairs(context: MatchContext, buckets: dict, names_match, records_match, adjust) -> None:
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
                        merge_and_audit(
                            context, rid_a, rid_b, f"{name_a} ↔ {name_b}", score, signals,
                        )


# any pair of profiles
def _any_records(_p1: dict, _p2: dict) -> bool:
    return True


# same street, or the same full ZIP5
def same_street_or_zip(p1: dict, p2: dict) -> bool:
    z1, z2 = p1["zip5"], p2["zip5"]
    return bool(p1["streets"] & p2["streets"]) or bool(z1 and len(z1) == 5 and z1 == z2)


# cross-name bonus only with an anchor
def _cross_name_adjust(score: float, signals: list) -> float:
    if any(signal.startswith(("street(", "zip5=", "employer=")) for signal in signals):
        signals.append(f"cross_name(+{SCORE_CROSS_NAME_BONUS})")
        return score + SCORE_CROSS_NAME_BONUS
    signals.append("CROSS_NAME_NO_ANCHOR")
    return min(score, MERGE_THRESHOLD - 1)


# match first-name variants with street/ZIP/employer anchor
def score_cross_groups(context: MatchContext) -> None:
    score_bucket_pairs(
        context, _norms_by_last_name(context.name_groups),
        are_cross_group_candidates, _any_records, _cross_name_adjust,
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


# same street, or same ZIP5 and employer
def _same_street_or_zip_and_employer(p1: dict, p2: dict) -> bool:
    z1, z2 = p1["zip5"], p2["zip5"]
    same_zip = bool(z1 and len(z1) == 5 and z1 == z2)
    same_employer = bool(p1["norm_employers"] & p2["norm_employers"])
    return bool(p1["streets"] & p2["streets"]) or (same_zip and same_employer)


# surnames are spelling variants of each other
def _surnames_vary(name_a: str, name_b: str) -> bool:
    return is_surname_variant(name_a.split("|", 1)[0], name_b.split("|", 1)[0])


# tag surname-variant pair, score unchanged
def _surname_variant_adjust(score: float, signals: list) -> float:
    signals.append("surname_variant")
    return score


# match surname typos sharing street or ZIP+employer
def score_surname_variants(context: MatchContext) -> None:
    score_bucket_pairs(
        context, _norms_by_first_name(context.name_groups),
        _surnames_vary, _same_street_or_zip_and_employer, _surname_variant_adjust,
    )
