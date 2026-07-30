"""Build donor record profiles for matching."""

import pandas as pd

from .constants import STATUS_EMPLOYERS, GENERIC_OCC_CATEGORIES
from .normalize import normalize_name, extract_middle, normalize_employer


def _s(val) -> str:
    """Cell value as a stripped string, NaN -> ''."""
    if pd.isna(val):
        return ""
    return str(val).strip()


def build_profiles(indiv: pd.DataFrame) -> dict:
    """Build a per-record_id (NAME|CITY|STATE) profile aggregating streets, employers, and zips."""
    profiles = {}

    for _, row in indiv.iterrows():
        rid = (
            _s(row["contributor_name"]) + "|" +
            _s(row["contributor_city"]) + "|" +
            _s(row["contributor_state"])
        )

        if rid not in profiles:
            profiles[rid] = {
                "rid": rid,
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
            }

        p = profiles[rid]
        p["record_count"] += 1

        street = _s(row.get("contributor_street_1")).upper()
        if street and street != "NAN":
            p["streets"].add(street)

        emp = _s(row.get("contributor_employer")).upper()
        if emp and emp not in STATUS_EMPLOYERS:
            p["norm_employers"].add(normalize_employer(emp))

        occ_cat = _s(row.get("occupation_category")).upper()
        if occ_cat == "RETIRED":
            p["retired"] = True
        elif occ_cat and occ_cat not in GENERIC_OCC_CATEGORIES:
            p["occ_categories"].add(occ_cat)

    return profiles
