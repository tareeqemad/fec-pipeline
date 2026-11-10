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


def _can_merge_suffixes(context: MatchContext, profile_id_a: str, profile_id_b: str) -> bool:
    """JR and SR are two people; a suffix on one side only needs a shared street."""
    suffixes_a = context.component_suffixes[context.union_find.find(profile_id_a)]
    suffixes_b = context.component_suffixes[context.union_find.find(profile_id_b)]
    if len(suffixes_a | suffixes_b) > 1:
        return False
    if bool(suffixes_a) != bool(suffixes_b):
        streets_a = context.profiles[profile_id_a]["streets"]
        streets_b = context.profiles[profile_id_b]["streets"]
        return bool(streets_a & streets_b)
    return True


def _merge(context: MatchContext, profile_id_a: str, profile_id_b: str, score: int) -> None:
    """Join the two profiles' groups when the score and the identity rules allow."""
    if (
        score < MERGE_THRESHOLD
        or not _can_merge_suffixes(context, profile_id_a, profile_id_b)
        or identities_must_stay_separate(context.profiles[profile_id_a], context.profiles[profile_id_b])
    ):
        return
    root_a = context.union_find.find(profile_id_a)
    root_b = context.union_find.find(profile_id_b)
    if context.union_find.union(profile_id_a, profile_id_b):
        suffixes = context.component_suffixes.pop(root_a) | context.component_suffixes.pop(root_b)
        context.component_suffixes[context.union_find.find(profile_id_a)] = suffixes


def score_same_names(context: MatchContext) -> None:
    """Score all candidate pairs within each name group."""
    for profile_ids in context.name_groups.values():
        for profile_id_a, profile_id_b in combinations(profile_ids, 2):
            profile_a = context.profiles[profile_id_a]
            profile_b = context.profiles[profile_id_b]
            evidence = compare_profiles(profile_a, profile_b, len(profile_ids))
            _merge(context, profile_id_a, profile_id_b, evidence.score)


def _group_norms_by_last_name(name_groups: dict) -> dict:
    """Group the normalised names by surname."""
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" in norm_name:
            grouped[norm_name.split("|", 1)[0]].add(norm_name)
    return grouped


def _score_group_pairs(
    context: MatchContext,
    profile_ids_a: list,
    profile_ids_b: list,
    profiles_match: Callable[[dict, dict], bool],
    adjust: Callable[[Evidence], int],
) -> None:
    """Score every profile of one name group against every profile of another."""
    name_freq = len(profile_ids_a) + len(profile_ids_b)
    for profile_id_a in profile_ids_a:
        for profile_id_b in profile_ids_b:
            profile_a, profile_b = context.profiles[profile_id_a], context.profiles[profile_id_b]
            if not profiles_match(profile_a, profile_b):
                continue
            evidence = compare_profiles(profile_a, profile_b, name_freq)
            _merge(context, profile_id_a, profile_id_b, adjust(evidence))


def _score_bucket_pairs(
    context: MatchContext,
    buckets: dict,
    names_match: Callable[[str, str], bool],
    profiles_match: Callable[[dict, dict], bool],
    adjust: Callable[[Evidence], int],
) -> None:
    """Score eligible profile pairs in sorted name order."""
    for norm_names in buckets.values():
        for name_a, name_b in combinations(sorted(norm_names), 2):
            if not names_match(name_a, name_b):
                continue
            profile_ids_a = context.name_groups[name_a]
            profile_ids_b = context.name_groups[name_b]
            _score_group_pairs(context, profile_ids_a, profile_ids_b, profiles_match, adjust)


def _accept_any_profiles(_profile_a: dict, _profile_b: dict) -> bool:
    """Accept any pair of profiles."""
    return True


def _share_street(profile_a: dict, profile_b: dict) -> bool:
    """Return whether two profiles share a street."""
    return bool(profile_a["streets"] & profile_b["streets"])


def _share_street_or_zip(profile_a: dict, profile_b: dict) -> bool:
    """Return whether two profiles share a street or a five-digit ZIP."""
    zip_a, zip_b = profile_a["zip5"], profile_b["zip5"]
    same_zip = bool(zip_a and len(zip_a) == 5 and zip_a == zip_b)
    return bool(profile_a["streets"] & profile_b["streets"]) or same_zip


def _adjust_cross_name_score(evidence: Evidence) -> int:
    """Another first name counts only beside a shared street, ZIP or employer."""
    if evidence.anchored:
        return evidence.score + SCORE_CROSS_NAME_BONUS
    return min(evidence.score, MERGE_THRESHOLD - 1)


def _adjust_same_words_score(evidence: Evidence) -> int:
    """Score the same words in another order: a bonus unless the middle initials differ."""
    if evidence.middle_conflict:
        return evidence.score
    return evidence.score + SCORE_CROSS_NAME_BONUS


def _keep_score(evidence: Evidence) -> int:
    """Return the score unchanged."""
    return evidence.score


def score_first_name_variants(context: MatchContext) -> None:
    """First-name variants only with a shared street, ZIP or employer."""
    buckets = _group_norms_by_last_name(context.name_groups)
    first_names_by_surname = {
        last: {norm_name.split("|", 1)[1] for norm_name in norm_names} for last, norm_names in buckets.items()
    }

    def are_candidates(name_a: str, name_b: str) -> bool:
        """Return whether two names of one surname are cross-group candidates."""
        surname = name_a.split("|", 1)[0]
        return first_names_may_match(name_a, name_b, first_names_by_surname[surname])

    _score_bucket_pairs(context, buckets, are_candidates, _accept_any_profiles, _adjust_cross_name_score)


def _group_norms_by_first_name(name_groups: dict) -> dict:
    """Group the normalised names that have both parts by first name."""
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, first = norm_name.split("|", 1)
        if first and last:
            grouped[first].add(norm_name)
    return grouped


def _share_street_or_zip_and_employer(profile_a: dict, profile_b: dict) -> bool:
    """Return whether two profiles share a street, or both a ZIP and an employer."""
    zip_a, zip_b = profile_a["zip5"], profile_b["zip5"]
    same_zip = bool(zip_a and len(zip_a) == 5 and zip_a == zip_b)
    same_employer = bool(profile_a["norm_employers"] & profile_b["norm_employers"])
    return bool(profile_a["streets"] & profile_b["streets"]) or (same_zip and same_employer)


def _are_surname_variants(name_a: str, name_b: str) -> bool:
    """Return whether the surnames of two names are variants of each other."""
    return is_surname_variant(name_a.split("|", 1)[0], name_b.split("|", 1)[0])


def score_surname_variants(context: MatchContext) -> None:
    """Surname typos need a shared street, or ZIP and employer."""
    _score_bucket_pairs(
        context,
        _group_norms_by_first_name(context.name_groups),
        _are_surname_variants,
        _share_street_or_zip_and_employer,
        _keep_score,
    )


def _build_name_token_key(norm_name: str) -> frozenset:
    """Return the surname and first name, in either order."""
    last, _, first = norm_name.partition("|")
    tokens = _TOKEN_SPLIT_RE.split(f"{last} {first}")
    return frozenset(token for token in tokens if len(token) >= 2)


def _group_profiles_by_name_words(profiles: dict) -> dict:
    """Group the profiles by their filed name words and by their name tokens."""
    buckets = defaultdict(set)
    for profile_id, profile in profiles.items():
        words = filed_name_words(profile["name"])
        if len(words) >= 2:
            buckets[words].add(profile_id)
        tokens = _build_name_token_key(profile["norm_name"])
        if len(tokens) >= 2:
            buckets[tokens].add(profile_id)
    return buckets


def score_name_formats(context: MatchContext) -> None:
    """Other formats of one name, sharing a street or ZIP.

    FIRST, LAST beside LAST, FIRST; and the same words in another order or
    punctuation (LAST, MIDDLE FIRST; FIRST-MIDDLE), where the middle name
    is the same person's and not evidence against.
    """
    buckets = _group_profiles_by_name_words(context.profiles)
    seen = set()
    for profile_ids in buckets.values():
        for profile_id_a, profile_id_b in combinations(sorted(profile_ids), 2):
            profile_a = context.profiles[profile_id_a]
            profile_b = context.profiles[profile_id_b]
            pair = (profile_id_a, profile_id_b)
            if profile_a["norm_name"] == profile_b["norm_name"] or pair in seen:
                continue
            seen.add(pair)
            if not _share_street_or_zip(profile_a, profile_b):
                continue
            freq_a = len(context.name_groups[profile_a["norm_name"]])
            freq_b = len(context.name_groups[profile_b["norm_name"]])
            evidence = compare_profiles(profile_a, profile_b, freq_a + freq_b)
            _merge(context, profile_id_a, profile_id_b, _adjust_same_words_score(evidence))


def _index_superset_names(name_groups: dict) -> tuple[dict, dict, dict]:
    """Index each name's tokens, its first-name tokens, and the names holding each token."""
    tokens_of: dict = {}
    first_tokens_of: dict = {}
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, _, first = norm_name.partition("|")
        name_tokens = frozenset(token for token in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(token) >= 2)
        if len(name_tokens) >= 2:
            tokens_of[norm_name] = name_tokens
            first_tokens_of[norm_name] = frozenset(token for token in _TOKEN_SPLIT_RE.split(first) if len(token) >= 2)

    names_with_token: dict = defaultdict(set)
    for norm_name, name_tokens in tokens_of.items():
        for token in name_tokens:
            names_with_token[token].add(norm_name)
    return tokens_of, first_tokens_of, names_with_token


def _find_superset_candidates(tokens: frozenset, names_with_token: dict) -> set:
    """Return the names that hold every one of the tokens."""
    candidates = None
    for token in tokens:
        if candidates is None:
            candidates = set(names_with_token[token])
        else:
            candidates = candidates & names_with_token[token]
    return candidates


def _is_valid_superset(
    shorter_name: str,
    longer_name: str,
    shorter_tokens: frozenset,
    tokens_of: dict,
    first_tokens_of: dict,
) -> bool:
    """Return whether the longer name adds up to two words to the shorter one.

    The two names must also share a first name.
    """
    if longer_name == shorter_name:
        return False
    longer_tokens = tokens_of[longer_name]
    if not shorter_tokens < longer_tokens:
        return False
    extra = longer_tokens - shorter_tokens
    if len(extra) > 2 or extra <= GENERATION_SUFFIXES:
        return False
    return bool(first_tokens_of[shorter_name] & longer_tokens)


def score_compound_surnames(context: MatchContext) -> None:
    """Short and compound surnames only on the same exact street."""
    tokens_of, first_tokens_of, names_with_token = _index_superset_names(context.name_groups)
    for short_name, short_tokens in tokens_of.items():
        for long_name in _find_superset_candidates(short_tokens, names_with_token):
            if not _is_valid_superset(short_name, long_name, short_tokens, tokens_of, first_tokens_of):
                continue
            _score_group_pairs(
                context,
                context.name_groups[short_name],
                context.name_groups[long_name],
                _share_street,
                _adjust_same_words_score,
            )
