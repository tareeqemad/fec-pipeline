"""Exact, externally verified address corrections."""
import csv

import pandas as pd

from fec.env import ADDRESS_RULES_CSV

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"


# apply exact, externally verified address corrections to the frame
def apply_verified_address_fixes(df: pd.DataFrame) -> int:
    """Apply exact, externally verified address corrections."""
    changed = 0
    df[S2] = df[S2].astype(object)
    for (street, state, zipcode), fixed in _read_address_rules().items():
        fixed_street, fixed_unit, fixed_city, fixed_state, fixed_zip, source = fixed
        mask = (
            df[S1].fillna("").eq(street)
            & df[STATE].fillna("").eq(state)
            & df[ZIP].fillna("").eq(zipcode)
        )
        if not mask.any():
            continue
        df.loc[mask, "_address_rule"] = f"{source}: {street} | {state} | {zipcode}"
        row_changed = mask & df[S1].fillna("").ne(fixed_street)
        df.loc[mask, S1] = fixed_street
        if fixed_unit is not None:
            row_changed |= mask & df[S2].fillna("").ne(fixed_unit)
            df.loc[mask, S2] = fixed_unit
        if fixed_city is not None:
            row_changed |= mask & df[CITY].fillna("").ne(fixed_city)
            df.loc[mask, CITY] = fixed_city
        if fixed_state is not None:
            row_changed |= mask & df[STATE].fillna("").ne(fixed_state)
            df.loc[mask, STATE] = fixed_state
        if fixed_zip is not None:
            row_changed |= mask & df[ZIP].fillna("").ne(fixed_zip)
            df.loc[mask, ZIP] = fixed_zip
        changed += int(row_changed.sum())
    return changed


# load the address rules CSV into a lookup dict
def _read_address_rules() -> dict[tuple[str, str, str], tuple]:
    """Read exact address corrections."""
    if not ADDRESS_RULES_CSV.exists():
        raise FileNotFoundError(f"Missing address rules: {ADDRESS_RULES_CSV}")

    rules = {}
    fixed_fields = ("street", "unit", "city", "state", "zip")
    with ADDRESS_RULES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            key = tuple(
                (row.get(field) or "").strip().upper()
                for field in ("match_street", "match_state", "match_zip")
            )
            fixed = tuple((row.get(field) or "").strip() or None for field in fixed_fields)
            source = (row.get("source") or "").strip()
            if not all(key) or not fixed[0] or not source:
                raise ValueError(f"Invalid address rule on row {number}")
            if key in rules:
                raise ValueError(f"Duplicate address rule on row {number}: {key}")
            rules[key] = fixed + (source,)
    return rules
