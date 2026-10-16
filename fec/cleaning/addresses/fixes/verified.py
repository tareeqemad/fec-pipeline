"""Exact, externally verified address corrections."""
import csv

import pandas as pd

from fec.env import ADDRESS_RULES_CSV

S1, S2 = "contributor_street_1", "contributor_street_2"
CITY, STATE, ZIP = "contributor_city", "contributor_state", "contributor_zip"
# a rule's unit that removes the filed unit instead of writing one
CLEAR_UNIT = "[CLEAR]"


# apply exact, externally verified address corrections to the frame
def apply_verified_address_fixes(df: pd.DataFrame) -> int:
    """Apply exact, externally verified address corrections."""
    rules = _read_address_rules()
    changed = 0
    df[S2] = df[S2].astype(object)
    for (street, state, zipcode, match_unit, match_sub_id), fixed in rules.items():
        fixed_street, fixed_unit, fixed_city, fixed_state, fixed_zip, source = fixed
        mask = (
            df[S1].fillna("").eq(street)
            & df[STATE].fillna("").eq(state)
            & df[ZIP].fillna("").eq(zipcode)
        )
        if match_unit is not None:
            # Match the normalized unit before applying any part of the rule.
            mask &= df[S2].fillna("").eq(match_unit)
        if match_sub_id is not None:
            if "sub_id" not in df.columns:
                continue
            mask &= df["sub_id"].fillna("").astype(str).str.strip().eq(match_sub_id)
        if not mask.any():
            continue
        df.loc[mask, "_address_rule"] = f"{source}: {street} | {state} | {zipcode}"
        row_changed = mask & df[S1].fillna("").ne(fixed_street)
        df.loc[mask, S1] = fixed_street
        if fixed_unit == CLEAR_UNIT:
            row_changed |= mask & df[S2].fillna("").ne("")
            df.loc[mask, S2] = pd.NA
        elif fixed_unit is not None:
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
def _read_address_rules() -> dict[tuple[str, str, str, str | None, str | None], tuple]:
    """Read exact corrections; unit clearing requires an explicit unit match."""
    if not ADDRESS_RULES_CSV.exists():
        raise FileNotFoundError(f"Missing address rules: {ADDRESS_RULES_CSV}")

    rules = {}
    fixed_fields = ("street", "unit", "city", "state", "zip")
    with ADDRESS_RULES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            address = tuple(
                (row.get(field) or "").strip().upper()
                for field in ("match_street", "match_state", "match_zip")
            )
            fixed = tuple((row.get(field) or "").strip() or None for field in fixed_fields)
            source = (row.get("source") or "").strip()
            match_unit = (row.get("match_unit") or "").strip().upper() or None
            match_sub_id = (row.get("match_sub_id") or "").strip() or None
            if not all(address) or not fixed[0] or not source:
                raise ValueError(f"Invalid address rule on row {number}")
            if fixed[1] == CLEAR_UNIT and match_unit is None:
                raise ValueError(
                    f"Invalid address rule on row {number}: [CLEAR] requires match_unit"
                )
            key = address + (match_unit, match_sub_id)
            if key in rules:
                raise ValueError(f"Duplicate address rule on row {number}: {key}")
            rules[key] = fixed + (source,)
    return rules
