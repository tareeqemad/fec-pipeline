"""Main matching engine: orchestrates scoring phases and builds donor clusters."""

import hashlib
import re
from collections import defaultdict
from typing import Tuple

import pandas as pd

from fec.log import get_logger

from .constants import (
    MERGE_THRESHOLD, FORCE_MERGE_NAMES, FORCE_MERGE_GROUPS,
    SCORE_CROSS_NAME_BONUS, _is_blocked_merge,
)
from .structures import UnionFind
from .profiles import build_profiles
from .scoring import compute_score, _are_cross_group_candidates, _is_surname_variant

logger = get_logger(__name__)


def match_donors(df: pd.DataFrame, threshold: int = MERGE_THRESHOLD,
                 verbose: bool = True) -> Tuple[dict, list]:
    """
    Score-based donor matching.
    Returns (rid_to_key, audit_log).
    """
    indiv = df[df["entity_type"] == "INDIVIDUAL"].copy()

    if verbose:
        logger.info(f"\n  \u2500\u2500 Building profiles \u2500\u2500")

    profiles = build_profiles(indiv)

    if verbose:
        logger.info(f"  \u2713 {len(profiles):,} unique record profiles")

    # Component-name rarity (# of distinct full names sharing a surname / first
    # name). A distinctive name \u2014 rare surname OR rare first name \u2014 is required
    # before merging across states without geographic corroboration, so common
    # names (DAVID MOORE \u2026) that coincidentally appear twice are never fused.
    last_to_names = defaultdict(set)
    first_to_names = defaultdict(set)
    for nn in {p["norm_name"] for p in profiles.values()}:
        last, _, first = nn.partition("|")
        last_to_names[last].add(nn)
        first_to_names[first].add(nn)
    for p in profiles.values():
        last, _, first = p["norm_name"].partition("|")
        p["last_freq"] = len(last_to_names[last])
        p["first_freq"] = len(first_to_names[first])

    # Count name frequency
    norm_name_counts = defaultdict(int)
    for p in profiles.values():
        norm_name_counts[p["norm_name"]] += 1

    # Group by normalized name
    name_groups = defaultdict(list)
    for rid, p in profiles.items():
        name_groups[p["norm_name"]].append(rid)

    # Initialize union-find
    uf = UnionFind()
    for rid in profiles:
        uf.find(rid)

    audit_log = []
    score_dist = defaultdict(int)

    # Phase 1: Score within-group pairs
    merge_count, skip_count = _score_within_groups(
        name_groups, norm_name_counts, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2: Cross-group matching (nicknames + typos)
    cross_merge, cross_skip = _score_cross_groups(
        name_groups, norm_name_counts, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.5: Surname-variant matching (typos/space forms ACROSS surnames)
    sv_merge, sv_skip = _score_surname_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.6: Name-format variants (first/last swap, missing comma, middle initial)
    nv_merge, nv_skip = _score_name_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.7: Maiden/married (one name's tokens ⊂ the other + same street)
    ss_merge, ss_skip = _score_surname_superset_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 3: Chain validation
    components = _build_and_validate_chains(
        uf, profiles, norm_name_counts, verbose
    )

    # Phase 4: Force-merge verified pairs
    _apply_force_merges(components, profiles, verbose)

    # Build donor_key mapping
    rid_to_key = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for rid in members:
            rid_to_key[rid] = key

    unique_before = len(profiles)
    unique_after = len(components)

    if verbose:
        logger.info(f"\n  \u2500\u2500 Results \u2500\u2500")
        logger.info(f"  Pairs scored:   {merge_count + skip_count + cross_merge + cross_skip + sv_merge + sv_skip + nv_merge + nv_skip + ss_merge + ss_skip:>7,}")
        logger.info(f"  Merged (\u2265{threshold}):  {merge_count + cross_merge + sv_merge + nv_merge + ss_merge:>7,}")
        logger.info(f"  Skipped (<{threshold}): {skip_count + cross_skip + sv_skip + nv_skip + ss_skip:>7,}")
        logger.info(f"  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
        logger.info(f"  Before: {unique_before:>6,} unique records")
        logger.info(f"  After:  {unique_after:>6,} unique donors")
        logger.info(f"  Merged: {unique_before - unique_after:>6,} (-{(unique_before - unique_after) / unique_before * 100:.1f}%)")

        logger.info(f"\n  \u2500\u2500 Score Distribution \u2500\u2500")
        for bucket in sorted(score_dist):
            bar = "\u2588" * min(50, score_dist[bucket] // max(1, max(score_dist.values()) // 50))
            marker = " \u25c4 THRESHOLD" if bucket <= threshold < bucket + 10 else ""
            logger.info(f"    {bucket:>4}\u2013{bucket+9:<4}: {score_dist[bucket]:>5,}  {bar}{marker}")

        near = [a for a in audit_log if threshold - 15 <= a["score"] <= threshold + 15]
        near.sort(key=lambda x: -x["score"])
        if near:
            logger.info(f"\n  \u2500\u2500 Near Threshold (\u00b115) \u2014 {len(near)} pairs \u2500\u2500")
            for a in near[:15]:
                status = "\u2713 MERGE" if a["merged"] else "\u2717 SKIP "
                name_a = a["rid_a"].split("|")[0] if "|" in a["rid_a"] else a["rid_a"][:30]
                city_a = a["rid_a"].split("|")[1] if "|" in a["rid_a"] else ""
                city_b = a["rid_b"].split("|")[1] if "|" in a["rid_b"] else ""
                logger.info(f"    [{a['score']:>3}] {status}  {name_a}: {city_a} \u2194 {city_b}")
                logger.info(f"                    {a['signals']}")

    return rid_to_key, audit_log


def _score_within_groups(
    name_groups: dict, norm_name_counts: dict, profiles: dict,
    uf: UnionFind, audit_log: list, score_dist: dict,
    threshold: int, verbose: bool,
) -> Tuple[int, int]:
    """Score all candidate pairs within each name group."""
    merge_count = 0
    skip_count = 0

    if verbose:
        logger.info(f"\n  \u2500\u2500 Scoring pairs (threshold={threshold}) \u2500\u2500")

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
    name_groups: dict, norm_name_counts: dict, profiles: dict,
    uf: UnionFind, audit_log: list, score_dist: dict,
    threshold: int, verbose: bool,
) -> Tuple[int, int]:
    """Cross-group matching: nicknames + first-name typos. Requires street match."""
    if verbose:
        logger.info(f"\n  \u2500\u2500 Cross-group matching (nicknames + typos) \u2500\u2500")

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
                        score_dist[bucket] = score_dist.get(bucket, 0) + 1

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


def _score_surname_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> Tuple[int, int]:
    """Match records whose SURNAMES are spelling variants but first names match.

    Phases 1–2 never compare across different surnames (the surname is a hard
    identity anchor), so spelling variants — SHTREMBERG/SHTEREMBERG,
    DE TOLEDO/DETOLEDO, an initial glued on as A SPIEGEL — stay split. This
    phase fills that gap, blocking by first name and pairing surname variants.

    SAFETY: a merge requires a STRONG geographic signal — the same street or the
    same ZIP5 — on top of the usual score threshold. A shared first name + a
    big-employer coincidence (same company, same state) is NOT enough, so two
    different families are never fused on a near-miss surname alone.
    """
    first_to_norms = defaultdict(set)
    for nn in name_groups:
        if "|" in nn:
            last, first = nn.split("|", 1)
            if first and last:
                first_to_norms[first].add(nn)

    merged = 0
    skipped = 0
    for first, norms in first_to_norms.items():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            last_i = norm_list[i].split("|", 1)[0]
            for j in range(i + 1, len(norm_list)):
                last_j = norm_list[j].split("|", 1)[0]
                if not _is_surname_variant(last_i, last_j):
                    continue

                rids_a = name_groups[norm_list[i]]
                rids_b = name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = profiles[ra], profiles[rb]

                        z1, z2 = p1["zip5"], p2["zip5"]
                        strong_geo = bool(p1["streets"] & p2["streets"]) or (
                            z1 and len(z1) == 5 and z1 == z2
                        )
                        if not strong_geo:
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        signals.append("surname_variant")
                        score_dist[(score // 10) * 10] += 1

                        did_merge = False
                        if score >= threshold:
                            if _is_blocked_merge(p1["name"], p2["name"]):
                                skipped += 1
                                signals.append("BLOCKED(do_not_merge)")
                            elif uf.union(ra, rb):
                                merged += 1
                                did_merge = True
                        else:
                            skipped += 1

                        audit_log.append({
                            "rid_a": ra,
                            "rid_b": rb,
                            "norm_name": f"{norm_list[i]} ↔ {norm_list[j]}",
                            "score": score,
                            "merged": did_merge,
                            "signals": "; ".join(signals),
                        })

    if verbose:
        logger.info(f"\n  ── Surname-variant matching ──")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _name_token_key(norm_name: str) -> frozenset:
    """Order-independent set of a name's real tokens (length >= 2, so single-char
    initials drop out and word order / a missing comma don't matter). Suffixes
    (JR/SR/II/III) are length >= 2 and therefore KEPT — they stay distinct so a
    father/son ('… JR' vs no suffix) never collapse."""
    last, _, first = norm_name.partition("|")
    return frozenset(t for t in re.split(r"[\s,]+", f"{last} {first}") if len(t) >= 2)


def _score_name_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> Tuple[int, int]:
    """Match the SAME person written in a different name FORMAT — first/last
    swapped, a missing comma, or a middle initial added/dropped — which parse to
    different norm_names and so are never compared by Phases 1-2.

    Bucket by the set of real name tokens (so order, comma and initials don't
    matter) and let compute_score decide. SAFETY is layered: compute_score's
    middle-name logic HARD_BLOCKs a genuine middle conflict (DARIUS J vs DARIUS K
    = father/son), suffixes stay in the key (JR != no-JR), and a STRONG
    geographic signal (same street or same ZIP5) is required — so two different
    people are never fused on a name-format coincidence.
    """
    token_to_norms = defaultdict(set)
    for nn in name_groups:
        if "|" not in nn:
            continue
        key = _name_token_key(nn)
        if len(key) >= 2:
            token_to_norms[key].add(nn)

    merged = 0
    skipped = 0
    for key, norms in token_to_norms.items():
        if len(norms) < 2:
            continue
        norm_list = sorted(norms)
        for i in range(len(norm_list)):
            for j in range(i + 1, len(norm_list)):
                rids_a = name_groups[norm_list[i]]
                rids_b = name_groups[norm_list[j]]
                combined_freq = len(rids_a) + len(rids_b)

                for ra in rids_a:
                    for rb in rids_b:
                        p1, p2 = profiles[ra], profiles[rb]
                        z1, z2 = p1["zip5"], p2["zip5"]
                        strong_geo = bool(p1["streets"] & p2["streets"]) or (
                            z1 and len(z1) == 5 and z1 == z2
                        )
                        if not strong_geo:
                            continue

                        score, signals = compute_score(p1, p2, combined_freq)
                        # the matching token set is itself identity evidence
                        # (like a nickname match) — give the cross-name credit,
                        # but ONLY when there is no middle conflict, so a
                        # same-street father/son with different middle initials
                        # (penalised -30) is never tipped over the threshold.
                        if not any(('MIDDLE_CONFLICT' in s) or ('HARD_BLOCK' in s) for s in signals):
                            score += SCORE_CROSS_NAME_BONUS
                        signals.append("name_format_variant")
                        score_dist[(score // 10) * 10] += 1

                        did_merge = False
                        if score >= threshold:
                            if _is_blocked_merge(p1["name"], p2["name"]):
                                skipped += 1
                                signals.append("BLOCKED(do_not_merge)")
                            elif uf.union(ra, rb):
                                merged += 1
                                did_merge = True
                        else:
                            skipped += 1

                        audit_log.append({
                            "rid_a": ra,
                            "rid_b": rb,
                            "norm_name": f"{norm_list[i]} ↔ {norm_list[j]}",
                            "score": score,
                            "merged": did_merge,
                            "signals": "; ".join(signals),
                        })

    if verbose:
        logger.info(f"\n  ── Name-format-variant matching ──")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


_SUFFIX_TOKENS = frozenset({'JR', 'SR', 'II', 'III', 'IV', 'V'})


def _score_surname_superset_variants(
    name_groups: dict, profiles: dict, uf: UnionFind,
    audit_log: list, score_dist: dict, threshold: int, verbose: bool,
) -> Tuple[int, int]:
    """Merge a maiden/short name into the married/compound form of one person.

    One name's token set is a PROPER SUBSET of the other and the extra token(s)
    are a real surname (not a JR/SR suffix) — 'SANDERS, JILL' ⊂
    'HARMATZ, JILL SANDERS', 'MOSSANEN, DORA' ⊂ 'LEVY MOSSANEN, DORA'. Gated on a
    SHARED first-name token AND the SAME exact street. Those two gates make it
    safe: a spouse has a DIFFERENT first name (so never a token-subset with a
    shared first name), and a father/son differs only by a suffix (excluded).
    """
    key_of: dict = {}
    first_of: dict = {}
    for nn in name_groups:
        if '|' not in nn:
            continue
        last, _, first = nn.partition('|')
        toks = frozenset(t for t in re.split(r'[\s,]+', f'{last} {first}') if len(t) >= 2)
        if len(toks) >= 2:
            key_of[nn] = toks
            first_of[nn] = frozenset(t for t in re.split(r'[\s,]+', first) if len(t) >= 2)

    tok_index: dict = defaultdict(set)
    for nn, toks in key_of.items():
        for t in toks:
            tok_index[t].add(nn)

    merged = 0
    skipped = 0
    seen_pairs = set()
    for nn1, k1 in key_of.items():
        # norms whose token set contains ALL of k1's tokens = supersets of nn1
        cand = None
        for t in k1:
            cand = set(tok_index[t]) if cand is None else (cand & tok_index[t])
        if not cand:
            continue
        for nn2 in cand:
            if nn2 == nn1:
                continue
            k2 = key_of[nn2]
            if not (k1 < k2):                      # require PROPER subset
                continue
            extra = k2 - k1
            if len(extra) > 2 or extra <= _SUFFIX_TOKENS:  # suffix-only diff = father/son
                continue
            # the shorter name's first-name token must appear in the longer name
            if not (first_of.get(nn1, frozenset()) & k2):
                continue
            pair = (nn1, nn2)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            combined_freq = len(name_groups[nn1]) + len(name_groups[nn2])
            for ra in name_groups[nn1]:
                for rb in name_groups[nn2]:
                    p1, p2 = profiles[ra], profiles[rb]
                    if not (p1['streets'] & p2['streets']):   # SAME exact street only
                        continue
                    score, signals = compute_score(p1, p2, combined_freq)
                    if not any(('MIDDLE_CONFLICT' in s) or ('HARD_BLOCK' in s) for s in signals):
                        score += SCORE_CROSS_NAME_BONUS
                    signals.append("surname_superset")
                    score_dist[(score // 10) * 10] += 1

                    did_merge = False
                    if score >= threshold:
                        if _is_blocked_merge(p1["name"], p2["name"]):
                            skipped += 1
                            signals.append("BLOCKED(do_not_merge)")
                        elif uf.union(ra, rb):
                            merged += 1
                            did_merge = True
                    else:
                        skipped += 1

                    audit_log.append({
                        "rid_a": ra, "rid_b": rb,
                        "norm_name": f"{nn1} ⊂ {nn2}",
                        "score": score, "merged": did_merge,
                        "signals": "; ".join(signals),
                    })

    if verbose:
        logger.info(f"\n  ── Maiden/married (surname-superset) matching ──")
        logger.info(f"  Merged: {merged:>5,}  |  Skipped: {skipped:>5,}")

    return merged, skipped


def _build_and_validate_chains(
    uf: UnionFind, profiles: dict, norm_name_counts: dict, verbose: bool,
) -> dict:
    """Build components from union-find, then validate large clusters."""
    CHAIN_MIN = 30
    CHAIN_CLUSTER_MIN = 4

    components = defaultdict(set)
    for rid in uf.parent:
        root = uf.find(rid)
        components[root].add(rid)

    n_chain_broken = 0
    for root, members in list(components.items()):
        if len(members) < CHAIN_CLUSTER_MIN:
            continue

        canonical_rid = max(members, key=lambda r: profiles[r]["record_count"])
        p_canon = profiles[canonical_rid]
        canon_freq = norm_name_counts.get(p_canon["norm_name"], 1)

        ejected = set()
        for rid in members:
            if rid == canonical_rid:
                continue
            p = profiles[rid]
            score, _ = compute_score(p_canon, p, canon_freq)
            if score < CHAIN_MIN:
                ejected.add(rid)

        if ejected:
            n_chain_broken += len(ejected)
            components[root] -= ejected
            for rid in ejected:
                components[rid] = {rid}

    if verbose and n_chain_broken:
        logger.info(f"\n  \u2500\u2500 Chain validation \u2500\u2500")
        logger.info(f"  Ejected {n_chain_broken} transitively-chained members from large clusters")

    return components


def _merge_roots_of(components: dict, rids: list) -> int:
    """Fold every component that contains one of ``rids`` into a single one.

    Returns the number of extra roots folded in (0 when fewer than 2 distinct
    roots are involved, i.e. nothing to merge).
    """
    roots = set()
    for rid in rids:
        for root, members in components.items():
            if rid in members:
                roots.add(root)
                break

    if len(roots) < 2:
        return 0

    roots_list = sorted(roots)
    target = roots_list[0]
    folded = 0
    for other_root in roots_list[1:]:
        if other_root in components:
            components[target] |= components[other_root]
            del components[other_root]
            folded += 1
    return folded


def _apply_force_merges(components: dict, profiles: dict, verbose: bool) -> None:
    """Force-merge human-verified same-person records.

    Two override sources, both from ``data/database/donor_overrides.csv``:
      • FORCE_MERGE_NAMES  — one (last, first) split across clusters/states.
      • FORCE_MERGE_GROUPS — distinct surname SPELLINGS of one person
        (KHODARI / KHODAR / KHIDARI), which the scorer never compares because
        the surname is a hard identity anchor.
    """
    n_force = 0
    for (force_last, force_first) in FORCE_MERGE_NAMES:
        rids = [
            rid for rid, p in profiles.items()
            if p["name"].startswith(f"{force_last}, {force_first}")
        ]
        if len(rids) >= 2:
            n_force += _merge_roots_of(components, rids)

    n_group = 0
    for identities in FORCE_MERGE_GROUPS.values():
        rids = [
            rid for rid, p in profiles.items()
            if any(p["name"].startswith(f"{last}, {first}") for (last, first) in identities)
        ]
        if len(rids) >= 2:
            n_group += _merge_roots_of(components, rids)

    if verbose and (n_force or n_group):
        logger.info(
            f"  Force-merged {n_force} multi-state pair(s) + "
            f"{n_group} cross-surname group(s)"
        )
