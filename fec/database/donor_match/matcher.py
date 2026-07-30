"""Main matching engine: orchestrates scoring phases and builds donor clusters."""

import hashlib
from collections import defaultdict

import pandas as pd

from fec.log import get_logger

from .constants import MERGE_THRESHOLD, FORCE_MERGE_NAMES, FORCE_MERGE_GROUPS
from .structures import UnionFind
from .scoring import compute_score
from .profiles import build_profiles
from .pairs import _score_within_groups, _score_cross_groups
from .surnames import (
    _score_surname_variants, _score_surname_superset_variants,
    _score_name_variants,
)

logger = get_logger(__name__)

# chain validation: in clusters of CHAIN_CLUSTER_MIN+ members, anyone scoring
# below CHAIN_MIN against the canonical record is ejected
CHAIN_MIN = 30
CHAIN_CLUSTER_MIN = 4


def match_donors(df: pd.DataFrame, threshold: int = MERGE_THRESHOLD,
                 verbose: bool = True) -> tuple[dict, list]:
    """Score-based donor matching; returns (rid_to_key, audit_log)."""
    indiv = df[df["entity_type"] == "INDIVIDUAL"].copy()

    if verbose:
        logger.info("\n  -- Building profiles --")

    profiles = build_profiles(indiv)

    if verbose:
        logger.info(f"  {len(profiles):,} unique record profiles")

    # component-name rarity: a rare surname OR rare first name is required before
    # a cross-state merge without geography, so common names are never fused
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

    norm_name_counts = defaultdict(int)
    for p in profiles.values():
        norm_name_counts[p["norm_name"]] += 1

    name_groups = defaultdict(list)
    for rid, p in profiles.items():
        name_groups[p["norm_name"]].append(rid)

    uf = UnionFind()
    for rid in profiles:
        uf.find(rid)

    audit_log = []
    score_dist = defaultdict(int)

    # Phase 1: score within-group pairs
    merge_count, skip_count = _score_within_groups(
        name_groups, norm_name_counts, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2: cross-group matching (nicknames + typos)
    cross_merge, cross_skip = _score_cross_groups(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.5: surname-variant matching (typos/space forms across surnames)
    sv_merge, sv_skip = _score_surname_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.6: name-format variants (first/last swap, missing comma, middle initial)
    nv_merge, nv_skip = _score_name_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 2.7: maiden/married (one name's tokens a subset of the other + same street)
    ss_merge, ss_skip = _score_surname_superset_variants(
        name_groups, profiles, uf, audit_log, score_dist, threshold, verbose
    )

    # Phase 3: chain validation
    components = _build_and_validate_chains(
        uf, profiles, norm_name_counts, verbose
    )

    # Phase 4: force-merge verified pairs
    _apply_force_merges(components, profiles, verbose)

    rid_to_key = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for rid in members:
            rid_to_key[rid] = key

    unique_before = len(profiles)
    unique_after = len(components)

    if verbose:
        logger.info("\n  -- Results --")
        logger.info(f"  Pairs scored:   {merge_count + skip_count + cross_merge + cross_skip + sv_merge + sv_skip + nv_merge + nv_skip + ss_merge + ss_skip:>7,}")
        logger.info(f"  Merged (>={threshold}):  {merge_count + cross_merge + sv_merge + nv_merge + ss_merge:>7,}")
        logger.info(f"  Skipped (<{threshold}): {skip_count + cross_skip + sv_skip + nv_skip + ss_skip:>7,}")
        logger.info("  -----------------------------")
        logger.info(f"  Before: {unique_before:>6,} unique records")
        logger.info(f"  After:  {unique_after:>6,} unique donors")
        logger.info(f"  Merged: {unique_before - unique_after:>6,} (-{(unique_before - unique_after) / unique_before * 100:.1f}%)")

        logger.info("\n  -- Score Distribution --")
        for bucket in sorted(score_dist):
            bar = "#" * min(50, score_dist[bucket] // max(1, max(score_dist.values()) // 50))
            marker = " <- THRESHOLD" if bucket <= threshold < bucket + 10 else ""
            logger.info(f"    {bucket:>4}-{bucket+9:<4}: {score_dist[bucket]:>5,}  {bar}{marker}")

        near = [a for a in audit_log if threshold - 15 <= a["score"] <= threshold + 15]
        near.sort(key=lambda x: -x["score"])
        if near:
            logger.info(f"\n  -- Near Threshold (+/-15) - {len(near)} pairs --")
            for a in near[:15]:
                status = "MERGE" if a["merged"] else "SKIP "
                name_a = a["rid_a"].split("|")[0] if "|" in a["rid_a"] else a["rid_a"][:30]
                city_a = a["rid_a"].split("|")[1] if "|" in a["rid_a"] else ""
                city_b = a["rid_b"].split("|")[1] if "|" in a["rid_b"] else ""
                logger.info(f"    [{a['score']:>3}] {status}  {name_a}: {city_a} <-> {city_b}")
                logger.info(f"                    {a['signals']}")

    return rid_to_key, audit_log


def _build_and_validate_chains(
    uf: UnionFind, profiles: dict, norm_name_counts: dict, verbose: bool,
) -> dict:
    """Build components from union-find, then eject weakly-chained members of large clusters."""
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
        logger.info("\n  -- Chain validation --")
        logger.info(f"  Ejected {n_chain_broken} transitively-chained members from large clusters")

    return components


def _merge_roots_of(components: dict, rids: list) -> int:
    """Fold every component containing one of rids into a single one; returns extra roots folded (0 when fewer than 2 distinct roots)."""
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
    """Force-merge human-verified same-person records from donor_overrides.csv: FORCE_MERGE_NAMES pairs plus FORCE_MERGE_GROUPS cross-surname spellings."""
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
