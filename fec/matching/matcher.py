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
from fec.vocabulary.statuses import EMPLOYER_STATUS_VALUES
from fec.vocabulary.tables import words

PHASES = (
    score_same_names,
    score_first_name_variants,
    score_surname_variants,
    score_name_formats,
    score_compound_surnames,
)
# Generic categories support no match; retirement is scored separately.
_GENERIC_OCC_CATEGORIES = words("generic_occupation_categories")


def match_donors(df: pd.DataFrame) -> dict:
    """Score-based donor matching; returns each profile's donor key."""
    # A held filing (a joint name, an unproven owner) belongs to no donor and cannot join two people.
    unheld_people = df["entity_type"].eq("INDIVIDUAL") & held_groups(df).eq("")
    profiles = build_profiles(df[unheld_people])
    name_groups = defaultdict(list)
    for profile_id, profile in profiles.items():
        name_groups[profile["norm_name"]].append(profile_id)
    union_find = UnionFind()
    component_suffixes = {
        union_find.find(profile_id): ({profile["suffix"]} if profile["suffix"] else set())
        for profile_id, profile in profiles.items()
    }
    context = MatchContext(name_groups, profiles, union_find, component_suffixes)
    for phase in PHASES:
        phase(context)

    components = defaultdict(set)
    for profile_id in union_find.parent:
        components[union_find.find(profile_id)].add(profile_id)
    _apply_force_merges(components, profiles)
    return keys_for_groups(components)


def _clean_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def build_profiles(individuals: pd.DataFrame) -> dict:
    """Build profiles by cleaned name, location, and generation suffix."""
    profiles = {}
    for _, row in individuals.iterrows():
        name = _clean_cell(row["contributor_name"])
        city = _clean_cell(row["contributor_city"])
        state = _clean_cell(row["contributor_state"])
        zip_code = _clean_cell(row["contributor_zip"])
        suffix = _clean_cell(row.get("_generational_suffix")).upper()
        profile_id = individual_record_id(name, city, state, suffix, split_zip(name, zip_code))
        if profile_id not in profiles:
            profiles[profile_id] = {
                "name": name,
                "norm_name": normalize_name(row["contributor_name"]),
                "middle": extract_middle(row["contributor_name"]),
                "city": city.upper(),
                "state": state.upper(),
                "zip5": zip_code,
                "streets": set(),
                "norm_employers": set(),
                "occ_categories": set(),
                "retired": False,
                "suffix": suffix,
            }
        profile = profiles[profile_id]

        # Only a filed street can support a match.
        street = _clean_cell(row.get("contributor_street_1")).upper()
        if street and street != "NAN":
            profile["streets"].add(street)

        employer = _clean_cell(row.get("contributor_employer")).upper()
        if employer and employer not in EMPLOYER_STATUS_VALUES:
            profile["norm_employers"].add(normalize_employer(employer))

        category = _clean_cell(row.get("occupation_category")).upper()
        if category == "RETIRED":
            profile["retired"] = True
        elif category and category not in _GENERIC_OCC_CATEGORIES:
            profile["occ_categories"].add(category)
    return profiles


class UnionFind:
    """Track connected profiles with path compression and union by rank."""

    def __init__(self) -> None:
        """Start with no profiles."""
        self.parent = {}
        self.rank = {}

    def find(self, item: str) -> str:
        """Return the root of item's group, adding item as its own group if new."""
        if item not in self.parent:
            self.parent[item] = item
            self.rank[item] = 0
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, item_a: str, item_b: str) -> bool:
        """Join the groups of item_a and item_b; return False if they were already one group."""
        root_a, root_b = self.find(item_a), self.find(item_b)
        if root_a == root_b:
            return False
        if self.rank[root_a] < self.rank[root_b]:
            root_a, root_b = root_b, root_a
        self.parent[root_b] = root_a
        if self.rank[root_a] == self.rank[root_b]:
            self.rank[root_a] += 1
        return True


def _apply_force_merges(components: dict, profiles: dict) -> None:
    root_of = {profile_id: root for root, members in components.items() for profile_id in members}
    for prefixes in NAME_MERGES.values():
        roots = sorted(
            {root_of[profile_id] for profile_id, profile in profiles.items() if profile["name"].startswith(prefixes)}
        )
        for other_root in roots[1:]:
            for profile_id in components.pop(other_root):
                components[roots[0]].add(profile_id)
                root_of[profile_id] = roots[0]
