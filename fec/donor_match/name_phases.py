"""Match phases that pair spelling variants of a name."""
from collections import defaultdict

from fec.donor_match.constants import (
    SCORE_CROSS_NAME_BONUS,
)
from fec.donor_match.normalize import _GENERATION_SUFFIXES
from fec.donor_match.phases import _TOKEN_SPLIT_RE, MatchContext, _merge_and_audit
from fec.donor_match.scoring import compute_score


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
) -> None:
    """Match the same person written in a different name format, bucketed by token set and gated on same street or ZIP5; middle-name conflicts stay blocked/penalised."""
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
                        _merge_and_audit(
                            context,
                            ra,
                            rb,
                            f"{norm_list[i]} ↔ {norm_list[j]}",
                            score,
                            signals,
                        )


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
    if len(extra) > 2 or extra <= _GENERATION_SUFFIXES:
        return False
    return bool(first_of[shorter_name] & longer_tokens)


def _score_surname_superset_variants(
    context: MatchContext,
) -> None:
    """Match short and compound surnames only on the same exact street."""
    key_of, first_of, tok_index = _superset_name_index(context.name_groups)

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
                    _merge_and_audit(
                        context,
                        ra,
                        rb,
                        f"{nn1} ⊂ {nn2}",
                        score,
                        signals,
                    )
