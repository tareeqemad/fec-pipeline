"""Build profiles, match donors, and assign their keys."""

from collections import defaultdict

import pandas as pd

from fec.matching.donor_keys import individual_record_id, keys_for_groups
from fec.matching.identity_rules import NAME_MERGES, held_groups, split_zip
from fec.matching.name_compare import extract_middle, normalize_employer, normalize_name
from fec.matching.phases import (
    MatchContext,
    score_compound_surnames,
    score_first_name_variants,
    score_name_formats,
    score_same_names,
    score_surname_variants,
)
from fec.matching.scoring import GENERIC_OCC_CATEGORIES
from fec.vocabulary.statuses import EMPLOYER_STATUS_VALUES

PHASES = (
    score_same_names,
    score_first_name_variants,
    score_surname_variants,
    score_name_formats,
    score_compound_surnames,
)


def match_donors(df: pd.DataFrame) -> dict:
    """Score-based donor matching; returns each profile's donor key.

    A held filing (a joint name, an unproven owner) belongs to no donor, so
    it takes no part and cannot join two people through itself.
    """
    profiles = build_profiles(df[df["entity_type"].eq("INDIVIDUAL") & held_groups(df).eq("")])
    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        name_groups[profile["norm_name"]].append(rid)
    uf = UnionFind()
    context = MatchContext(
        name_groups=name_groups,
        profiles=profiles,
        union_find=uf,
        component_suffixes={uf.find(rid): ({p["suffix"]} if p["suffix"] else set()) for rid, p in profiles.items()},
    )
    for phase in PHASES:
        phase(context)

    components = defaultdict(set)
    for rid in uf.parent:
        components[uf.find(rid)].add(rid)
    _apply_force_merges(components, profiles)
    return keys_for_groups(components)


def _s(val: object) -> str:
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

    def __init__(self) -> None:
        """Start with no profiles."""
        self.parent = {}
        self.rank = {}

    def find(self, x: str) -> str:
        """Return the root of x's group, adding x as its own group if new."""
        if x not in self.parent:
            self.parent[x] = x
            self.rank[x] = 0
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        """Join the groups of a and b; return False if they were already one group."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if self.rank[ra] < self.rank[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        if self.rank[ra] == self.rank[rb]:
            self.rank[ra] += 1
        return True


def _apply_force_merges(components: dict, profiles: dict) -> None:
    """Join the groups of every profile a verified name merge covers."""
    root_of = {rid: root for root, members in components.items() for rid in members}
    for prefixes in NAME_MERGES.values():
        roots = sorted({root_of[rid] for rid, p in profiles.items() if p["name"].startswith(prefixes)})
        for other in roots[1:]:
            for rid in components.pop(other):
                components[roots[0]].add(rid)
                root_of[rid] = roots[0]
