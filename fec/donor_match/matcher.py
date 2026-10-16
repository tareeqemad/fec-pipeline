"""Build profiles, match donors, and assign their keys."""

from collections import defaultdict

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
from fec.donor_match.scoring import (
    GENERIC_OCC_CATEGORIES,
    compute_score,
)


def match_donors(df: pd.DataFrame) -> tuple[dict, list]:
    """Score-based donor matching; returns keys and an audit log."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"].copy()
    profiles = build_profiles(individuals)

    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        name_groups[profile["norm_name"]].append(rid)

    uf = UnionFind()
    for rid in profiles:
        uf.find(rid)

    audit_log = []
    context = MatchContext(
        name_groups=name_groups,
        profiles=profiles,
        union_find=uf,
        audit_log=audit_log,
        component_suffixes={
            rid: ({profile["suffix"]} if profile["suffix"] else set())
            for rid, profile in profiles.items()
        },
    )
    _score_within_groups(context)
    _score_cross_groups(context)
    _score_surname_variants(context)
    _score_name_variants(context)
    _score_surname_superset_variants(context)

    components = _build_and_validate_chains(uf, profiles, name_groups)
    _apply_force_merges(components, profiles)
    split_joint_filings(
        components, find_joint_filings(profiles, components), profiles,
    )
    rid_to_key = _donor_keys(components)
    _validate_merge_audit(rid_to_key, audit_log)
    return rid_to_key, audit_log


def _s(val) -> str:
    """Cell value as a stripped string, NaN -> ''."""
    if pd.isna(val):
        return ""
    return str(val).strip()


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
            split_zip(_s(row["contributor_name"]), _s(row.get("contributor_zip"))),
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

        # Only a filed street can support a match.
        street = "" if row.get("_street_inferred") is True else _s(row.get("contributor_street_1")).upper()
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


class UnionFind:
    """Track connected profiles with path compression and union by rank."""

    # init empty parent/rank maps for union-find
    def __init__(self):
        self.parent = {}
        self.rank = {}

    # find x's cluster root, compressing the path
    def find(self, x):
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    # merge two clusters by rank, return whether merged
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


def _has_signal(row: dict, prefix: str) -> bool:
    """Return whether an audit row contains a scoring signal with this prefix."""
    return any(
        signal.strip().startswith(prefix) for signal in row["signals"].split(";")
    )


# Check groups of four or more; separate members scoring below 30.
CHAIN_MIN = 30
CHAIN_CLUSTER_MIN = 4


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


def _build_and_validate_chains(
    uf: UnionFind,
    profiles: dict,
    name_groups: dict,
) -> dict:
    """Build components from union-find, then eject weakly-chained members of large clusters."""
    components = defaultdict(set)
    for rid in uf.parent:
        root = uf.find(rid)
        components[root].add(rid)

    for root, members in list(components.items()):
        if len(members) < CHAIN_CLUSTER_MIN:
            continue

        # Break equal counts by record ID so donor keys stay stable.
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
            components[root] -= ejected
            # Preserve this order: ejecting the root replaces its group,
            # leaving the other members to receive fallback keys.
            for rid in ejected:
                components[rid] = {rid}

    return components


def _merge_roots_of(components: dict, rids: list) -> int:
    """Merge the groups containing these profile IDs; return the number removed."""
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


def _apply_force_merges(components: dict, profiles: dict) -> None:
    """Apply verified name merges."""
    for prefixes in NAME_MERGES.values():
        rids = [rid for rid, p in profiles.items() if p["name"].startswith(prefixes)]
        if len(rids) >= 2:
            _merge_roots_of(components, rids)
