"""Main matching engine: orchestrates scoring phases and builds donor clusters."""

from collections import defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.donor_match.chains import (
    _apply_force_merges,
    _build_and_validate_chains,
    _validate_merge_audit,
)
from fec.donor_match.components import UnionFind, _donor_keys
from fec.donor_match.joint_split import find_joint_filings, split_joint_filings

from .constants import (
    GENERIC_OCC_CATEGORIES,
)
from .keys import individual_record_id
from .name_phases import _score_name_variants, _score_surname_superset_variants
from .normalize import extract_middle, normalize_employer, normalize_name
from .phases import (
    MatchContext,
    _score_cross_groups,
    _score_surname_variants,
    _score_within_groups,
)
from .rules import split_zip


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
    name_groups = defaultdict(list)
    for rid, profile in profiles.items():
        name_groups[profile["norm_name"]].append(rid)
    return name_groups


def _run_matching_phases(
    context: MatchContext,
) -> None:
    phases = (
        _score_within_groups,
        _score_cross_groups,
        _score_surname_variants,
        _score_name_variants,
        _score_surname_superset_variants,
    )
    for phase in phases:
        phase(context)


def match_donors(df: pd.DataFrame) -> tuple[dict, list]:
    """Score-based donor matching; returns keys and an audit log."""
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
    components = _build_and_validate_chains(uf, profiles, name_groups)
    _apply_force_merges(components, profiles)
    split_joint_filings(
        components, find_joint_filings(profiles, components), profiles,
    )
    rid_to_key = _donor_keys(components)
    _validate_merge_audit(rid_to_key, audit_log)
    return rid_to_key, audit_log


