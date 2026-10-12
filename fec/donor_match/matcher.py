"""Donor matching: scoring phases and clusters."""

from collections import defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.donor_match.chains import (
    apply_force_merges,
    build_and_validate_chains,
    validate_merge_audit,
)
from fec.donor_match.components import UnionFind, donor_keys
from fec.donor_match.constants import GENERIC_OCC_CATEGORIES
from fec.donor_match.joint_split import find_joint_filings, split_joint_filings
from fec.donor_match.keys import individual_record_id
from fec.donor_match.name_phases import (
    score_name_variants,
    score_surname_superset_variants,
)
from fec.donor_match.normalize import extract_middle, normalize_employer, normalize_name
from fec.donor_match.phases import (
    MatchContext,
    score_cross_groups,
    score_surname_variants,
    score_within_groups,
)
from fec.donor_match.rules import split_zip
from fec.io import cell_text




# one profile per name, location and suffix
def build_profiles(indiv: pd.DataFrame) -> dict:
    profiles = {}

    for _, row in indiv.iterrows():
        suffix = cell_text(row.get("_generational_suffix")).upper()
        rid = individual_record_id(
            cell_text(row["contributor_name"]),
            cell_text(row["contributor_city"]),
            cell_text(row["contributor_state"]),
            suffix,
            split_zip(cell_text(row["contributor_name"]), cell_text(row.get("contributor_zip"))),
        )

        if rid not in profiles:
            profiles[rid] = {
                "name": cell_text(row["contributor_name"]),
                "norm_name": normalize_name(row["contributor_name"]),
                "middle": extract_middle(row["contributor_name"]),
                "city": cell_text(row["contributor_city"]).upper(),
                "state": cell_text(row["contributor_state"]).upper(),
                "zip5": cell_text(row["contributor_zip"]),
                "streets": set(),
                "norm_employers": set(),
                "occ_categories": set(),
                "retired": False,
                "record_count": 0,
                "suffix": suffix,
            }

        p = profiles[rid]
        p["record_count"] += 1

        # inferred streets prove nothing about the filer
        street = "" if row.get("_street_inferred") is True else cell_text(row.get("contributor_street_1")).upper()
        if street and street != "NAN":
            p["streets"].add(street)

        emp = cell_text(row.get("contributor_employer")).upper()
        if emp and emp not in EMPLOYER_STATUS_VALUES:
            p["norm_employers"].add(normalize_employer(emp))

        occ_cat = cell_text(row.get("occupation_category")).upper()
        if occ_cat == "RETIRED":
            p["retired"] = True
        elif occ_cat and occ_cat not in GENERIC_OCC_CATEGORIES:
            p["occ_categories"].add(occ_cat)

    return profiles


# group profile ids by their normalized name
def _build_name_groups(profiles: dict) -> dict:
    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        name_groups[profile["norm_name"]].append(rid)
    return name_groups


# run every scoring phase in order
def _run_matching_phases(
    context: MatchContext,
) -> None:
    phases = (
        score_within_groups,
        score_cross_groups,
        score_surname_variants,
        score_name_variants,
        score_surname_superset_variants,
    )
    for phase in phases:
        phase(context)


# match donors; return keys and audit log
def match_donors(df: pd.DataFrame) -> tuple[dict, list]:
    individuals = df[df["entity_type"] == "INDIVIDUAL"].copy()
    profiles = build_profiles(individuals)

    name_groups = _build_name_groups(profiles)
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
    _run_matching_phases(context)
    components = build_and_validate_chains(uf, profiles, name_groups)
    apply_force_merges(components, profiles)
    split_joint_filings(
        components, find_joint_filings(profiles, components), profiles,
    )
    rid_to_key = donor_keys(components)
    validate_merge_audit(rid_to_key, audit_log)
    return rid_to_key, audit_log


