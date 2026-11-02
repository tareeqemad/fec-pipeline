"""Build profiles, match donors, and assign their keys."""

from collections import Counter, defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.donor_match.joint import find_joint_filings, split_joint_filings
from fec.donor_match.keys import _donor_keys, individual_record_id
from fec.donor_match.normalize import extract_middle, normalize_employer, normalize_name
from fec.donor_match.rules import NAME_MERGES, split_zip
from fec.donor_match.phases import (
    MatchContext,
    _score_cross_groups,
    _score_name_variants,
    _score_surname_superset_variants,
    _score_surname_variants,
    _score_within_groups,
)
from fec.donor_match.scoring import GENERIC_OCC_CATEGORIES, compute_score


CHAIN_MIN = 30
CHAIN_CLUSTER_MIN = 4


def match_donors(df: pd.DataFrame) -> tuple[dict, list]:
    """Score-based donor matching; returns keys and an audit log."""
    profiles = build_profiles(df.loc[df["entity_type"] == "INDIVIDUAL"])

    # A profile can match more than one candidate. Visit them in a stable order.
    name_groups = defaultdict(list)
    suffixes = {}
    for rid in sorted(profiles):
        profile = profiles[rid]
        name_groups[profile["norm_name"]].append(rid)
        suffix = profile["suffix"]
        suffixes[rid] = {suffix} if suffix else set()

    context = MatchContext(
        name_groups=name_groups,
        profiles=profiles,
        union_find=UnionFind(),
        audit_log=[],
        component_suffixes=suffixes,
    )
    _score_within_groups(context)
    _score_cross_groups(context)
    _score_surname_variants(context)
    _score_name_variants(context)
    _score_surname_superset_variants(context)

    components = _validated_components(context)
    _apply_force_merges(components, profiles)
    joint_filings = find_joint_filings(profiles, components)
    split_joint_filings(components, joint_filings, profiles)
    rid_to_key = _donor_keys(components)
    _validate_merge_audit(rid_to_key, context.audit_log)
    return rid_to_key, context.audit_log


def _text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def build_profiles(indiv: pd.DataFrame) -> dict:
    """Build profiles by cleaned name, location, and generation suffix."""
    profiles = {}
    zip_counts = defaultdict(Counter)

    for _, row in indiv.iterrows():
        filed_name = row["contributor_name"]
        name = _text(filed_name)
        city = _text(row["contributor_city"])
        state = _text(row["contributor_state"])
        suffix = _text(row.get("_generational_suffix")).upper()
        zipcode = _text(row.get("contributor_zip"))
        rid = individual_record_id(name, city, state, suffix, split_zip(name, zipcode))

        if rid not in profiles:
            profiles[rid] = {
                "name": name,
                "norm_name": normalize_name(filed_name),
                "middle": extract_middle(filed_name),
                "city": city.upper(),
                "state": state.upper(),
                "zip5": "",
                "streets": set(),
                "norm_employers": set(),
                "occ_categories": set(),
                "retired": False,
                "record_count": 0,
                "suffix": suffix,
            }

        profile = profiles[rid]
        profile["record_count"] += 1
        if zipcode:
            zip_counts[rid][zipcode] += 1

        # Only a filed street can support a match.
        if row.get("_street_inferred") is not True:
            street = _text(row.get("contributor_street_1")).upper()
            if street and street != "NAN":
                profile["streets"].add(street)

        employer = _text(row.get("contributor_employer")).upper()
        if employer and employer not in EMPLOYER_STATUS_VALUES:
            profile["norm_employers"].add(normalize_employer(employer))

        category = _text(row.get("occupation_category")).upper()
        if category == "RETIRED":
            profile["retired"] = True
        elif category and category not in GENERIC_OCC_CATEGORIES:
            profile["occ_categories"].add(category)

    # Prefer the most frequently filed ZIP. Resolve equal counts by ZIP.
    for rid, counts in zip_counts.items():
        most_filings = max(counts.values())
        profiles[rid]["zip5"] = min(zipcode for zipcode, count in counts.items() if count == most_filings)
    return profiles


class UnionFind:
    """Track connected profiles with path compression and union by rank."""

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


def _unsafe_merge(signals: str) -> bool:
    if any(block in signals for block in ("HARD_BLOCK", "BLOCKED(", "CROSS_NAME_NO_ANCHOR")):
        return True

    evidence = [item.strip() for item in signals.split(";")]
    if any(item.startswith("street(") for item in evidence):
        return False

    zip5 = any(item.startswith("zip5=") for item in evidence)
    employer = any(item.startswith("employer=") for item in evidence)
    cross_name = any(item.startswith("cross_name(") for item in evidence)
    return (
        "surname_superset" in signals
        or ("name_format_variant" in signals and not zip5)
        or ("surname_variant" in signals and not (zip5 and employer))
        or (cross_name and not (zip5 or employer))
    )


def _validate_merge_audit(rid_to_key: dict, audit_log: list) -> None:
    """Check only automatic merges that survived the final splits."""
    violations = [
        row for row in audit_log
        if row["merged"]
        and rid_to_key.get(row["rid_a"]) == rid_to_key.get(row["rid_b"])
        and _unsafe_merge(row["signals"])
    ]

    if violations:
        examples = ", ".join(
            f"{row['rid_a']} <-> {row['rid_b']}" for row in violations[:3]
        )
        raise ValueError(
            f"Donor identity quality gate rejected {len(violations)} unsafe "
            f"merge(s): {examples}"
        )


def _validated_components(context: MatchContext) -> dict:
    """Union-find components, ejecting weakly chained members of large clusters."""
    components = defaultdict(set)
    profiles = context.profiles
    for rid in profiles:
        components[context.union_find.find(rid)].add(rid)

    for root, members in list(components.items()):
        if len(members) < CHAIN_CLUSTER_MIN:
            continue

        # Break ties by record ID so keys stay stable.
        canonical_rid = max(
            members,
            key=lambda rid: (profiles[rid]["record_count"], rid),
        )
        canonical = profiles[canonical_rid]
        name_frequency = len(context.name_groups[canonical["norm_name"]])

        ejected = set()
        for rid in members - {canonical_rid}:
            score, _ = compute_score(canonical, profiles[rid], name_frequency)
            if score < CHAIN_MIN:
                ejected.add(rid)

        if ejected:
            retained_root = canonical_rid if root in ejected else root
            components[retained_root] = members - ejected
            for rid in ejected:
                components[rid] = {rid}

    return components


def _apply_force_merges(components: dict, profiles: dict) -> None:
    """Apply verified name merges."""
    for prefixes in NAME_MERGES.values():
        matches = {rid for rid, profile in profiles.items() if profile["name"].startswith(prefixes)}
        roots = sorted(root for root, members in components.items() if not members.isdisjoint(matches))
        if len(roots) < 2:
            continue
        keep, *others = roots
        for root in others:
            components[keep].update(components.pop(root))
