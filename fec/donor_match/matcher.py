"""Main matching engine: orchestrates scoring phases and builds donor clusters."""

import hashlib
from collections import defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.log import get_logger

from .constants import (
    MERGE_THRESHOLD,
    GENERIC_OCC_CATEGORIES,
)
from .joint import first_of_name, given_tokens, joint_partners
from .keys import individual_record_id
from .rules import NAME_MERGES, joint_name_exempt, resolve_donor_key
from .scoring import (
    compute_score,
    normalize_name,
    extract_middle,
    normalize_employer,
)
from .phases import (
    MatchContext,
    _score_within_groups,
    _score_cross_groups,
    _score_surname_variants,
    _score_name_variants,
    _score_surname_superset_variants,
)

logger = get_logger(__name__)

# chain validation: in clusters of CHAIN_CLUSTER_MIN+ members, anyone scoring
# below CHAIN_MIN against the canonical record is ejected
CHAIN_MIN = 30
CHAIN_CLUSTER_MIN = 4


class UnionFind:
    """Disjoint-set with path compression and union by rank - the cluster engine."""

    def __init__(self):
        self.parent = {}
        self.rank = {}

    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _s(val) -> str:
    """Cell value as a stripped string, NaN -> ''."""
    if pd.isna(val):
        return ""
    return str(val).strip()


def _has_signal(row: dict, prefix: str) -> bool:
    """Return whether an audit row contains a scoring signal with this prefix."""
    return any(
        signal.strip().startswith(prefix) for signal in row["signals"].split(";")
    )


def _validate_merge_audit(rid_to_key: dict, audit_log: list) -> dict:
    """Fail if a final automatic merge bypassed an identity safety rule."""
    final_merges = [
        row
        for row in audit_log
        if row["merged"]
        and rid_to_key.get(row["rid_a"]) == rid_to_key.get(row["rid_b"])
    ]
    violations = []

    for row in final_merges:
        street = _has_signal(row, "street(")
        zip5 = _has_signal(row, "zip5=")
        employer = _has_signal(row, "employer=")
        signals = row["signals"]

        if any(
            block in signals
            for block in (
                "HARD_BLOCK",
                "BLOCKED(",
                "CROSS_NAME_NO_ANCHOR",
            )
        ):
            violations.append(row)
        elif _has_signal(row, "cross_name(") and not (street or zip5 or employer):
            violations.append(row)
        elif "surname_variant" in signals and not (street or (zip5 and employer)):
            violations.append(row)
        elif "name_format_variant" in signals and not (street or zip5):
            violations.append(row)
        elif "surname_superset" in signals and not street:
            violations.append(row)

    if violations:
        examples = ", ".join(
            f"{row['rid_a']} <-> {row['rid_b']}" for row in violations[:3]
        )
        raise ValueError(
            f"Donor identity quality gate rejected {len(violations)} unsafe "
            f"merge(s): {examples}"
        )

    return {
        "checked": len(final_merges),
        "cross_name_zip_only": sum(
            _has_signal(row, "cross_name(")
            and _has_signal(row, "zip5=")
            and not _has_signal(row, "street(")
            and not _has_signal(row, "employer=")
            for row in final_merges
        ),
    }


def build_profiles(indiv: pd.DataFrame) -> dict:
    """Build profiles by cleaned name, location, and generation suffix."""
    profiles = {}

    for _, row in indiv.iterrows():
        suffix = _s(row.get("_generational_suffix")).upper()
        rid = individual_record_id(
            _s(row["contributor_name"]),
            _s(row["contributor_city"]),
            _s(row["contributor_state"]),
            suffix,
        )

        if rid not in profiles:
            profiles[rid] = {
                "name": _s(row["contributor_name"]),
                "norm_name": normalize_name(row["contributor_name"]),
                "middle": extract_middle(row["contributor_name"]),
                "city": _s(row["contributor_city"]).upper(),
                "state": _s(row["contributor_state"]).upper(),
                "zip5": _s(row["contributor_zip"]),
                "streets": set(),
                "norm_employers": set(),
                "occ_categories": set(),
                "retired": False,
                "record_count": 0,
                "suffix": suffix,
            }

        p = profiles[rid]
        p["record_count"] += 1

        street = _s(row.get("contributor_street_1")).upper()
        if street and street != "NAN":
            p["streets"].add(street)

        emp = _s(row.get("contributor_employer")).upper()
        if emp and emp not in EMPLOYER_STATUS_VALUES:
            p["norm_employers"].add(normalize_employer(emp))

        occ_cat = _s(row.get("occupation_category")).upper()
        if occ_cat == "RETIRED":
            p["retired"] = True
        elif occ_cat and occ_cat not in GENERIC_OCC_CATEGORIES:
            p["occ_categories"].add(occ_cat)

    return profiles


def _build_name_groups(profiles: dict) -> dict:
    last_to_names = defaultdict(set)
    first_to_names = defaultdict(set)
    for profile in profiles.values():
        last, _, first = profile["norm_name"].partition("|")
        last_to_names[last].add(profile["norm_name"])
        first_to_names[first].add(profile["norm_name"])

    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        last, _, first = profile["norm_name"].partition("|")
        profile["last_freq"] = len(last_to_names[last])
        profile["first_freq"] = len(first_to_names[first])
        name_groups[profile["norm_name"]].append(rid)
    return name_groups


def _run_matching_phases(
    context: MatchContext,
) -> list[tuple[int, int]]:
    phases = (
        _score_within_groups,
        _score_cross_groups,
        _score_surname_variants,
        _score_name_variants,
        _score_surname_superset_variants,
    )
    return [phase(context) for phase in phases]


def _donor_keys(components: dict) -> dict:
    rid_to_key = {}
    for root, members in components.items():
        key = hashlib.sha256(root.encode()).hexdigest()[:12]
        for rid in members:
            rid_to_key[rid] = key
    return rid_to_key


def _log_score_distribution(score_dist: dict) -> None:
    logger.info("\n  -- Score Distribution --")
    for bucket in sorted(score_dist):
        scale = max(1, max(score_dist.values()) // 50)
        bar = "#" * min(50, score_dist[bucket] // scale)
        marker = " <- THRESHOLD" if bucket <= MERGE_THRESHOLD < bucket + 10 else ""
        logger.info(
            f"    {bucket:>4}-{bucket + 9:<4}: {score_dist[bucket]:>5,}  {bar}{marker}"
        )


def _log_near_threshold(audit_log: list) -> None:
    near = [
        row
        for row in audit_log
        if MERGE_THRESHOLD - 15 <= row["score"] <= MERGE_THRESHOLD + 15
    ]
    near.sort(key=lambda row: -row["score"])
    if not near:
        return

    logger.info(f"\n  -- Near Threshold (+/-15) - {len(near)} pairs --")
    for row in near[:15]:
        status = "MERGE" if row["merged"] else "SKIP "
        rid_a = row["rid_a"]
        rid_b = row["rid_b"]
        name = rid_a.split("|")[0] if "|" in rid_a else rid_a[:30]
        city_a = rid_a.split("|")[1] if "|" in rid_a else ""
        city_b = rid_b.split("|")[1] if "|" in rid_b else ""
        logger.info(f"    [{row['score']:>3}] {status}  {name}: {city_a} <-> {city_b}")
        logger.info(f"                    {row['signals']}")


def _log_match_results(
    profiles: dict,
    components: dict,
    phase_counts: list[tuple[int, int]],
    identity_gate: dict,
    score_dist: dict,
    audit_log: list,
) -> None:
    merged = sum(counts[0] for counts in phase_counts)
    skipped = sum(counts[1] for counts in phase_counts)
    before = len(profiles)
    after = len(components)
    logger.info(
        "  Identity gate: "
        f"{identity_gate['checked']:,} final merge edges valid; "
        f"{identity_gate['cross_name_zip_only']:,} cross-name ZIP-only"
    )
    logger.info("\n  -- Results --")
    logger.info(f"  Pairs scored:   {merged + skipped:>7,}")
    logger.info(f"  Merged (>={MERGE_THRESHOLD}):  {merged:>7,}")
    logger.info(f"  Skipped (<{MERGE_THRESHOLD}): {skipped:>7,}")
    logger.info("  -----------------------------")
    logger.info(f"  Before: {before:>6,} unique records")
    logger.info(f"  After:  {after:>6,} unique donors")
    logger.info(
        f"  Merged: {before - after:>6,} (-{(before - after) / before * 100:.1f}%)"
    )
    _log_score_distribution(score_dist)
    _log_near_threshold(audit_log)


def _profile_spelling(profile: dict) -> tuple:
    return given_tokens(first_of_name(profile["name"]))


def _profile_last(profile: dict) -> str:
    return profile["norm_name"].partition("|")[0] if "|" in profile["norm_name"] else ""


def find_joint_filings(profiles: dict, components: dict) -> dict:
    """rid -> co-filer names, for profiles whose first name also names another person.

    Each component, joined with the others its verified merge_keys rules
    point to, stands for one person; its streets and ZIPs are the household.
    A first name is a joint filing when a different person of the same
    surname in that household files the extra word as their own given name
    (joint.joint_partners), unless a verified identity rule says the word is
    the filer's own name (rules.joint_name_exempt).
    """
    rid_to_key = _donor_keys(components)
    person_of = {rid: resolve_donor_key(key) for rid, key in rid_to_key.items()}
    members = defaultdict(list)
    for rid, person in person_of.items():
        members[person].append(rid)

    spellings = {rid: _profile_spelling(p) for rid, p in profiles.items()}
    by_place = defaultdict(set)  # (surname, street or zip5) -> persons
    places = defaultdict(set)    # person -> streets and zip5s
    for rid, person in person_of.items():
        profile = profiles[rid]
        last = _profile_last(profile)
        spots = {("S", street) for street in profile["streets"]}
        if len(profile["zip5"]) == 5:
            spots.add(("Z", profile["zip5"]))
        places[person] |= spots
        if last:
            for spot in spots:
                by_place[(last, spot)].add(person)

    signatures = {}
    for rid, person in person_of.items():
        spelling = spellings[rid]
        # a one-word name can only be a glued pair ("ELLENSTU") of a filer
        # who also files under another spelling
        if not spelling or (len(spelling) < 2 and len(members[person]) < 2):
            continue
        profile = profiles[rid]
        if joint_name_exempt(profile["name"]):
            continue
        last = _profile_last(profile)
        others = set()
        for spot in places[person]:
            others |= by_place.get((last, spot), set())
        others.discard(person)
        if not others:
            continue
        household = {
            spellings[other_rid]
            for other in others
            for other_rid in members[other]
            if _profile_last(profiles[other_rid]) == last
        }
        own = {spellings[member] for member in members[person]}
        partners = joint_partners(spelling, own, household)
        if partners:
            signatures[rid] = partners
    return signatures


def split_joint_filings(components: dict, signatures: dict, profiles: dict) -> int:
    """Give each joint filing its own donor, apart from its filer's solo records.

    "SPELLMAN, MARC MELISSA" joined Marc's records by scoring. Its rows move to
    one donor per co-filer pair; Marc's solo records stay together exactly as
    they were matched (the joint filing may still have been the evidence that
    linked two of his addresses: it is his filing too). The part holding the
    union-find root keeps that root and so its donor_key; every other part is
    keyed by its most-filed record. Returns the number of profiles moved.
    """
    moved = 0
    for root, members in list(components.items()):
        parts = defaultdict(set)
        for rid in members:
            parts[signatures.get(rid, frozenset())].add(rid)
        if len(parts) < 2:
            continue
        del components[root]
        for signature, part in sorted(parts.items(), key=lambda item: sorted(item[0])):
            if root in part:
                new_root = root
            else:
                ranked = sorted(
                    part,
                    key=lambda rid: (profiles[rid]["record_count"], rid),
                    reverse=True,
                )
                new_root = next(rid for rid in ranked if rid not in components)
            components[new_root] = part
            if signature:
                moved += len(part)
    return moved


def match_donors(df: pd.DataFrame, verbose: bool = True) -> tuple[dict, list]:
    """Score-based donor matching; returns keys and an audit log."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"].copy()
    if verbose:
        logger.info("\n  -- Building profiles --")
    profiles = build_profiles(individuals)
    if verbose:
        logger.info(f"  {len(profiles):,} unique record profiles")

    name_groups = _build_name_groups(profiles)
    uf = UnionFind()
    for rid in profiles:
        uf.find(rid)

    audit_log = []
    score_dist = defaultdict(int)
    context = MatchContext(
        name_groups=name_groups,
        profiles=profiles,
        union_find=uf,
        audit_log=audit_log,
        score_dist=score_dist,
        verbose=verbose,
        component_suffixes={
            rid: ({profile["suffix"]} if profile["suffix"] else set())
            for rid, profile in profiles.items()
        },
    )
    phase_counts = _run_matching_phases(context)
    components = _build_and_validate_chains(uf, profiles, name_groups, verbose)
    _apply_force_merges(components, profiles, verbose)
    moved = split_joint_filings(
        components, find_joint_filings(profiles, components), profiles,
    )
    if verbose and moved:
        logger.info(f"  {moved:,} joint-filing profiles moved off their filer's donor")
    rid_to_key = _donor_keys(components)
    identity_gate = _validate_merge_audit(rid_to_key, audit_log)
    if verbose:
        _log_match_results(
            profiles,
            components,
            phase_counts,
            identity_gate,
            score_dist,
            audit_log,
        )
    return rid_to_key, audit_log


def _build_and_validate_chains(
    uf: UnionFind,
    profiles: dict,
    name_groups: dict,
    verbose: bool,
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

        # The rid tie-break makes chain ejections and donor keys repeatable
        # across processes; set iteration order is hash-randomized.
        canonical_rid = max(
            members,
            key=lambda rid: (profiles[rid]["record_count"], rid),
        )
        p_canon = profiles[canonical_rid]
        canon_freq = len(name_groups[p_canon["norm_name"]])

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
            # if the union-find root itself is ejected (root != canonical_rid),
            # components[rid] = {rid} below clobbers the survivors' set and they
            # fall back to per-rid keys in apply_donor_key. Reordering this would
            # change existing donor_key values, so it is documented, not changed.
            for rid in ejected:
                components[rid] = {rid}

    if verbose and n_chain_broken:
        logger.info("\n  -- Chain validation --")
        logger.info(
            f"  Ejected {n_chain_broken} transitively-chained members from large clusters"
        )

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
    for other_root in roots_list[1:]:
        components[target] |= components[other_root]
        del components[other_root]
    return len(roots_list) - 1


def _apply_force_merges(components: dict, profiles: dict, verbose: bool) -> None:
    """Apply verified name merges."""
    merged = 0
    for prefixes in NAME_MERGES.values():
        rids = [rid for rid, p in profiles.items() if p["name"].startswith(prefixes)]
        if len(rids) >= 2:
            merged += _merge_roots_of(components, rids)

    if verbose and merged:
        logger.info("  Applied %s curated name merge(s)", f"{merged:,}")
