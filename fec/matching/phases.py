"""Compare donor pairs in the five matching phases.

1. the same normalised name (LAST|FIRST)
2. first-name variants of one surname: nickname, typo, truncation
3. surname variants, with strong geography
4. other formats of one name: FIRST, LAST or the words reordered
5. a surname inside a compound surname, on the same street
"""

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations

from fec.matching.identity_rules import identities_must_stay_separate
from fec.matching.name_compare import (
    GENERATION_SUFFIXES,
    filed_name_words,
    first_names_may_match,
    is_surname_variant,
)
from fec.matching.scoring import MERGE_THRESHOLD, SCORE_CROSS_NAME_BONUS, Evidence, compare_profiles

_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


@dataclass
class MatchContext:
    """Hold the name groups, profiles and union-find state shared by the matching phases."""

    name_groups: dict
    profiles: dict
    union_find: object
    component_suffixes: dict


def _merge(context: MatchContext, profile_id_a: str, profile_id_b: str, score: int) -> None:
    if score < MERGE_THRESHOLD:
        return
    profile_a, profile_b = context.profiles[profile_id_a], context.profiles[profile_id_b]
    root_a, root_b = context.union_find.find(profile_id_a), context.union_find.find(profile_id_b)
    suffixes = context.component_suffixes[root_a] | context.component_suffixes[root_b]
    if len(suffixes) > 1:
        return
    # A suffix on one side only (JR beside no suffix) joins only on a shared street.
    one_sided = bool(context.component_suffixes[root_a]) != bool(context.component_suffixes[root_b])
    if one_sided and not _share_street(profile_a, profile_b):
        return
    if identities_must_stay_separate(profile_a, profile_b):
        return
    if context.union_find.union(profile_id_a, profile_id_b):
        del context.component_suffixes[root_a], context.component_suffixes[root_b]
        context.component_suffixes[context.union_find.find(profile_id_a)] = suffixes


def score_same_names(context: MatchContext) -> None:
    """Score all candidate pairs within each name group."""
    for profile_ids in context.name_groups.values():
        for profile_id_a, profile_id_b in combinations(profile_ids, 2):
            profile_a, profile_b = context.profiles[profile_id_a], context.profiles[profile_id_b]
            evidence = compare_profiles(profile_a, profile_b, len(profile_ids))
            _merge(context, profile_id_a, profile_id_b, evidence.score)


def _group_norms_by_name_part(name_groups: dict) -> tuple[dict, dict]:
    by_last, by_first = defaultdict(set), defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, first = norm_name.split("|", 1)
        by_last[last].add(norm_name)
        if first and last:
            by_first[first].add(norm_name)
    return by_last, by_first


def _score_group_pairs(
    context: MatchContext,
    profile_ids_a: list,
    profile_ids_b: list,
    profiles_match: Callable[[dict, dict], bool] | None = None,
    adjust: Callable[[Evidence], int] | None = None,
) -> None:
    name_freq = len(profile_ids_a) + len(profile_ids_b)
    for profile_id_a in profile_ids_a:
        for profile_id_b in profile_ids_b:
            profile_a, profile_b = context.profiles[profile_id_a], context.profiles[profile_id_b]
            if profiles_match and not profiles_match(profile_a, profile_b):
                continue
            evidence = compare_profiles(profile_a, profile_b, name_freq)
            score = adjust(evidence) if adjust else evidence.score
            _merge(context, profile_id_a, profile_id_b, score)


def _score_bucket_pairs(
    context: MatchContext,
    buckets: dict,
    names_match: Callable[[str, str], bool],
    profiles_match: Callable[[dict, dict], bool] | None = None,
    adjust: Callable[[Evidence], int] | None = None,
) -> None:
    for norm_names in buckets.values():
        for name_a, name_b in combinations(sorted(norm_names), 2):
            if names_match(name_a, name_b):
                ids_a, ids_b = context.name_groups[name_a], context.name_groups[name_b]
                _score_group_pairs(context, ids_a, ids_b, profiles_match, adjust)


def _share_street(profile_a: dict, profile_b: dict) -> bool:
    return bool(profile_a["streets"] & profile_b["streets"])


def _share_zip5(profile_a: dict, profile_b: dict) -> bool:
    return len(profile_a["zip5"]) == 5 and profile_a["zip5"] == profile_b["zip5"]


def _share_street_or_zip_and_employer(profile_a: dict, profile_b: dict) -> bool:
    same_employer = bool(profile_a["norm_employers"] & profile_b["norm_employers"])
    return _share_street(profile_a, profile_b) or (_share_zip5(profile_a, profile_b) and same_employer)


def _adjust_cross_name_score(evidence: Evidence) -> int:
    if evidence.anchored:
        return evidence.score + SCORE_CROSS_NAME_BONUS
    return min(evidence.score, MERGE_THRESHOLD - 1)


def _adjust_same_words_score(evidence: Evidence) -> int:
    if evidence.middle_conflict:
        return evidence.score
    return evidence.score + SCORE_CROSS_NAME_BONUS


def score_first_name_variants(context: MatchContext) -> None:
    """First-name variants only with a shared street, ZIP or employer."""
    buckets, _ = _group_norms_by_name_part(context.name_groups)
    first_names_by_surname = {
        last: {norm_name.split("|", 1)[1] for norm_name in norm_names} for last, norm_names in buckets.items()
    }

    def are_candidates(name_a: str, name_b: str) -> bool:
        surname = name_a.split("|", 1)[0]
        return first_names_may_match(name_a, name_b, first_names_by_surname[surname])

    _score_bucket_pairs(context, buckets, are_candidates, adjust=_adjust_cross_name_score)


def _are_surname_variants(name_a: str, name_b: str) -> bool:
    return is_surname_variant(name_a.split("|", 1)[0], name_b.split("|", 1)[0])


def score_surname_variants(context: MatchContext) -> None:
    """Surname typos need a shared street, or ZIP and employer."""
    _, buckets = _group_norms_by_name_part(context.name_groups)
    _score_bucket_pairs(context, buckets, _are_surname_variants, _share_street_or_zip_and_employer)


def _long_tokens(text: str) -> frozenset:
    return frozenset(token for token in _TOKEN_SPLIT_RE.split(text) if len(token) >= 2)


def _group_profiles_by_name_words(profiles: dict) -> dict:
    buckets = defaultdict(set)
    for profile_id, profile in profiles.items():
        words = filed_name_words(profile["name"])
        if len(words) >= 2:
            buckets[words].add(profile_id)
        last, _, first = profile["norm_name"].partition("|")
        tokens = _long_tokens(f"{last} {first}")
        if len(tokens) >= 2:
            buckets[tokens].add(profile_id)
    return buckets


def score_name_formats(context: MatchContext) -> None:
    """Other formats of one name (FIRST, LAST; LAST, MIDDLE FIRST; FIRST-MIDDLE), sharing a street or ZIP."""
    buckets = _group_profiles_by_name_words(context.profiles)
    seen = set()
    for profile_ids in buckets.values():
        for profile_id_a, profile_id_b in combinations(sorted(profile_ids), 2):
            profile_a, profile_b = context.profiles[profile_id_a], context.profiles[profile_id_b]
            pair = (profile_id_a, profile_id_b)
            if profile_a["norm_name"] == profile_b["norm_name"] or pair in seen:
                continue
            seen.add(pair)
            if not (_share_street(profile_a, profile_b) or _share_zip5(profile_a, profile_b)):
                continue
            freq_a = len(context.name_groups[profile_a["norm_name"]])
            freq_b = len(context.name_groups[profile_b["norm_name"]])
            evidence = compare_profiles(profile_a, profile_b, freq_a + freq_b)
            # The middle name here is the same person's, not evidence against.
            _merge(context, profile_id_a, profile_id_b, _adjust_same_words_score(evidence))


def _index_superset_names(name_groups: dict) -> tuple[dict, dict, dict]:
    tokens_of, first_tokens_of, names_with_token = {}, {}, defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, _, first = norm_name.partition("|")
        name_tokens = _long_tokens(f"{last} {first}")
        if len(name_tokens) >= 2:
            tokens_of[norm_name] = name_tokens
            first_tokens_of[norm_name] = _long_tokens(first)
            for token in name_tokens:
                names_with_token[token].add(norm_name)
    return tokens_of, first_tokens_of, names_with_token


def _is_valid_superset(short_tokens: frozenset, long_tokens: frozenset, short_first_tokens: frozenset) -> bool:
    """Return whether the longer name adds up to two words to the shorter one and shares its first name."""
    if not short_tokens < long_tokens:
        return False
    extra = long_tokens - short_tokens
    if len(extra) > 2 or extra <= GENERATION_SUFFIXES:
        return False
    return bool(short_first_tokens & long_tokens)


def score_compound_surnames(context: MatchContext) -> None:
    """Short and compound surnames only on the same exact street."""
    tokens_of, first_tokens_of, names_with_token = _index_superset_names(context.name_groups)
    for short_name, short_tokens in tokens_of.items():
        # The names holding every word of the short name, the short name itself included.
        candidates = set.intersection(*(names_with_token[token] for token in short_tokens))
        for long_name in candidates:
            if not _is_valid_superset(short_tokens, tokens_of[long_name], first_tokens_of[short_name]):
                continue
            short_ids, long_ids = context.name_groups[short_name], context.name_groups[long_name]
            _score_group_pairs(context, short_ids, long_ids, _share_street, _adjust_same_words_score)
