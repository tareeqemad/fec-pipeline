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
from fec.matching.name_compare import GENERATION_SUFFIXES, filed_name_words, first_names_may_match, is_surname_variant
from fec.matching.scoring import MERGE_THRESHOLD, SCORE_CROSS_NAME_BONUS, Evidence, compare

_TOKEN_SPLIT_RE = re.compile(r"[\s,]+")


@dataclass
class MatchContext:
    """Hold the name groups, profiles and union-find state shared by the matching phases."""

    name_groups: dict
    profiles: dict
    union_find: object
    component_suffixes: dict


def _suffixes_allow_merge(context: MatchContext, rid_a: str, rid_b: str) -> bool:
    """JR and SR are two people; a suffix on one side only needs a shared street."""
    suffixes_a = context.component_suffixes[context.union_find.find(rid_a)]
    suffixes_b = context.component_suffixes[context.union_find.find(rid_b)]
    if len(suffixes_a | suffixes_b) > 1:
        return False
    if bool(suffixes_a) != bool(suffixes_b):
        return bool(context.profiles[rid_a]["streets"] & context.profiles[rid_b]["streets"])
    return True


def _merge(context: MatchContext, rid_a: str, rid_b: str, score: int) -> None:
    """Join the two profiles' groups when the score and the identity rules allow."""
    if (
        score < MERGE_THRESHOLD
        or not _suffixes_allow_merge(context, rid_a, rid_b)
        or identities_must_stay_separate(context.profiles[rid_a], context.profiles[rid_b])
    ):
        return
    root_a, root_b = context.union_find.find(rid_a), context.union_find.find(rid_b)
    if context.union_find.union(rid_a, rid_b):
        suffixes = context.component_suffixes.pop(root_a) | context.component_suffixes.pop(root_b)
        context.component_suffixes[context.union_find.find(rid_a)] = suffixes


def score_same_names(context: MatchContext) -> None:
    """Score all candidate pairs within each name group."""
    for rids in context.name_groups.values():
        for rid_a, rid_b in combinations(rids, 2):
            _merge(context, rid_a, rid_b, compare(context.profiles[rid_a], context.profiles[rid_b], len(rids)).score)


def _norms_by_last_name(name_groups: dict) -> dict:
    """Group the normalised names by surname."""
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" in norm_name:
            grouped[norm_name.split("|", 1)[0]].add(norm_name)
    return grouped


def _score_bucket_pairs(
    context: MatchContext,
    buckets: dict,
    names_match: Callable[[str, str], bool],
    records_match: Callable[[dict, dict], bool],
    adjust: Callable[[Evidence], int],
) -> None:
    """Score eligible profile pairs in sorted name order."""
    for norms in buckets.values():
        for name_a, name_b in combinations(sorted(norms), 2):
            if not names_match(name_a, name_b):
                continue
            rids_a, rids_b = context.name_groups[name_a], context.name_groups[name_b]
            for rid_a in rids_a:
                for rid_b in rids_b:
                    p1, p2 = context.profiles[rid_a], context.profiles[rid_b]
                    if records_match(p1, p2):
                        _merge(context, rid_a, rid_b, adjust(compare(p1, p2, len(rids_a) + len(rids_b))))


def _any_records(_p1: dict, _p2: dict) -> bool:
    """Accept any pair of profiles."""
    return True


def _same_street_or_zip(p1: dict, p2: dict) -> bool:
    """Return whether two profiles share a street or a five-digit ZIP."""
    z1, z2 = p1["zip5"], p2["zip5"]
    return bool(p1["streets"] & p2["streets"]) or bool(z1 and len(z1) == 5 and z1 == z2)


def _cross_name_adjust(evidence: Evidence) -> int:
    """Another first name counts only beside a shared street, ZIP or employer."""
    if evidence.anchored:
        return evidence.score + SCORE_CROSS_NAME_BONUS
    return min(evidence.score, MERGE_THRESHOLD - 1)


def _same_words_adjust(evidence: Evidence) -> int:
    """Score the same words in another order: a bonus unless the middle initials differ."""
    return evidence.score if evidence.middle_conflict else evidence.score + SCORE_CROSS_NAME_BONUS


def score_first_name_variants(context: MatchContext) -> None:
    """First-name variants only with a shared street, ZIP or employer."""
    buckets = _norms_by_last_name(context.name_groups)
    firsts = {last: {norm.split("|", 1)[1] for norm in norms} for last, norms in buckets.items()}

    def candidates(name_a: str, name_b: str) -> bool:
        """Return whether two names of one surname are cross-group candidates."""
        return first_names_may_match(name_a, name_b, firsts[name_a.split("|", 1)[0]])

    _score_bucket_pairs(context, buckets, candidates, _any_records, _cross_name_adjust)


def _norms_by_first_name(name_groups: dict) -> dict:
    """Group the normalised names that have both parts by first name."""
    grouped = defaultdict(set)
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, first = norm_name.split("|", 1)
        if first and last:
            grouped[first].add(norm_name)
    return grouped


def _same_street_or_zip_and_employer(p1: dict, p2: dict) -> bool:
    """Return whether two profiles share a street, or both a ZIP and an employer."""
    z1, z2 = p1["zip5"], p2["zip5"]
    same_zip = bool(z1 and len(z1) == 5 and z1 == z2)
    same_employer = bool(p1["norm_employers"] & p2["norm_employers"])
    return bool(p1["streets"] & p2["streets"]) or (same_zip and same_employer)


def _surnames_vary(name_a: str, name_b: str) -> bool:
    """Return whether the surnames of two names are variants of each other."""
    return is_surname_variant(name_a.split("|", 1)[0], name_b.split("|", 1)[0])


def score_surname_variants(context: MatchContext) -> None:
    """Surname typos need a shared street, or ZIP and employer."""
    _score_bucket_pairs(
        context,
        _norms_by_first_name(context.name_groups),
        _surnames_vary,
        _same_street_or_zip_and_employer,
        lambda evidence: evidence.score,
    )


def _name_token_key(norm_name: str) -> frozenset:
    """Return the surname and first name, in either order."""
    last, _, first = norm_name.partition("|")
    return frozenset(t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2)


def score_name_formats(context: MatchContext) -> None:
    """Other formats of one name, sharing a street or ZIP.

    FIRST, LAST beside LAST, FIRST; and the same words in another order or
    punctuation (LAST, MIDDLE FIRST; FIRST-MIDDLE), where the middle name
    is the same person's and not evidence against.
    """
    buckets = defaultdict(set)
    for rid, profile in context.profiles.items():
        words = filed_name_words(profile["name"])
        if len(words) >= 2:
            buckets[words].add(rid)
        tokens = _name_token_key(profile["norm_name"])
        if len(tokens) >= 2:
            buckets[tokens].add(rid)
    seen = set()
    for rids in buckets.values():
        for rid_a, rid_b in combinations(sorted(rids), 2):
            p1, p2 = context.profiles[rid_a], context.profiles[rid_b]
            if p1["norm_name"] == p2["norm_name"] or (rid_a, rid_b) in seen:
                continue
            seen.add((rid_a, rid_b))
            if not _same_street_or_zip(p1, p2):
                continue
            freq = len(context.name_groups[p1["norm_name"]]) + len(context.name_groups[p2["norm_name"]])
            _merge(context, rid_a, rid_b, _same_words_adjust(compare(p1, p2, freq)))


def _superset_name_index(name_groups: dict) -> tuple[dict, dict, dict]:
    """Index each name's tokens, its first-name tokens, and the names holding each token."""
    key_of: dict = {}
    first_of: dict = {}
    for norm_name in name_groups:
        if "|" not in norm_name:
            continue
        last, _, first = norm_name.partition("|")
        toks = frozenset(t for t in _TOKEN_SPLIT_RE.split(f"{last} {first}") if len(t) >= 2)
        if len(toks) >= 2:
            key_of[norm_name] = toks
            first_of[norm_name] = frozenset(t for t in _TOKEN_SPLIT_RE.split(first) if len(t) >= 2)

    tok_index: dict = defaultdict(set)
    for norm_name, tokens in key_of.items():
        for token in tokens:
            tok_index[token].add(norm_name)
    return key_of, first_of, tok_index


def _superset_candidates(tokens: frozenset, token_index: dict) -> set:
    """Return the names that hold every one of the tokens."""
    candidates = None
    for token in tokens:
        candidates = set(token_index[token]) if candidates is None else candidates & token_index[token]
    return candidates


def _is_valid_superset(
    shorter_name: str,
    longer_name: str,
    shorter_tokens: frozenset,
    key_of: dict,
    first_of: dict,
) -> bool:
    """Return whether the longer name adds up to two words to the shorter one and shares a first name."""
    if longer_name == shorter_name:
        return False
    longer_tokens = key_of[longer_name]
    if not shorter_tokens < longer_tokens:
        return False
    extra = longer_tokens - shorter_tokens
    if len(extra) > 2 or extra <= GENERATION_SUFFIXES:
        return False
    return bool(first_of[shorter_name] & longer_tokens)


def score_compound_surnames(context: MatchContext) -> None:
    """Short and compound surnames only on the same exact street."""
    key_of, first_of, tok_index = _superset_name_index(context.name_groups)
    for nn1, k1 in key_of.items():
        for nn2 in _superset_candidates(k1, tok_index):
            if not _is_valid_superset(nn1, nn2, k1, key_of, first_of):
                continue
            combined_freq = len(context.name_groups[nn1]) + len(context.name_groups[nn2])
            for ra in context.name_groups[nn1]:
                for rb in context.name_groups[nn2]:
                    p1, p2 = context.profiles[ra], context.profiles[rb]
                    if p1["streets"] & p2["streets"]:
                        _merge(context, ra, rb, _same_words_adjust(compare(p1, p2, combined_freq)))
