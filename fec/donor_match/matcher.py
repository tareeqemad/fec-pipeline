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
from fec.donor_match.scoring import GENERIC_OCC_CATEGORIES

PHASES = (
    _score_within_groups, _score_cross_groups, _score_surname_variants,
    _score_name_variants, _score_surname_superset_variants,
)


def match_donors(df: pd.DataFrame) -> dict:
    """Score-based donor matching; returns each profile's donor key."""
    profiles = build_profiles(df[df["entity_type"] == "INDIVIDUAL"])
    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        name_groups[profile["norm_name"]].append(rid)
    uf = UnionFind()
    context = MatchContext(
        name_groups=name_groups, profiles=profiles, union_find=uf,
        component_suffixes={uf.find(rid): ({p["suffix"]} if p["suffix"] else set()) for rid, p in profiles.items()},
    )
    for phase in PHASES:
        phase(context)

    components = defaultdict(set)
    for rid in uf.parent:
        components[uf.find(rid)].add(rid)
    edges = context.edges + _apply_force_merges(components, profiles)
    split_joint_filings(components, find_joint_filings(profiles, components), profiles, edges)
    return _donor_keys(components)


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


def _apply_force_merges(components: dict, profiles: dict) -> list:
    """Join the groups of every profile a verified name merge covers; return the pairs joined."""
    root_of = {rid: root for root, members in components.items() for rid in members}
    edges = []
    for prefixes in NAME_MERGES.values():
        rids = sorted(rid for rid, p in profiles.items() if p["name"].startswith(prefixes))
        if len(rids) < 2:
            continue
        target, *others = sorted({root_of[rid] for rid in rids})
        for other in others:
            for rid in components.pop(other):
                components[target].add(rid)
                root_of[rid] = target
        edges += list(zip(rids, rids[1:]))
    return edges
