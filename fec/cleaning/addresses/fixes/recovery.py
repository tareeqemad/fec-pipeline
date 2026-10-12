"""Recover missing address fields from donor's filings."""

from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.addresses.fixes.safe_text import is_state_zip_fragment
from fec.config.streets import FLOOR_ONLY_RE

# post-normalized types only; unit keywords excluded
STREET_TYPE_RE = re.compile(
    r"\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|"
    r"ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|"
    r"RUN|PT|PASS|WALK|BEND|MALL)\b"
)


# fill null streets from same name/city/state rows
def recover_null_streets(df: pd.DataFrame) -> int:
    null_street = df["contributor_street_1"].isna()
    if not null_street.any():
        return 0

    has_street = df["contributor_street_1"].notna()
    street_lookup = (
        df.loc[has_street]
        .groupby(["contributor_name", "contributor_city", "contributor_state"])[
            "contributor_street_1"
        ]
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )

    n_recovered = 0
    for idx in df[null_street].index:
        key = (
            df.at[idx, "contributor_name"],
            df.at[idx, "contributor_city"],
            df.at[idx, "contributor_state"],
        )
        street = street_lookup.get(key)
        if street:
            # keep the unit this filing named
            unit = _unit_text(df.at[idx, "contributor_street_1"])
            df.at[idx, "contributor_street_1"] = street
            second = df.at[idx, "contributor_street_2"] if "contributor_street_2" in df.columns else ""
            if unit and (pd.isna(second) or not str(second).strip()):
                df.at[idx, "contributor_street_2"] = unit
            n_recovered += 1

    return n_recovered


# true where street_1 looks geocodable, not floor-only
def is_usable_street(s: pd.Series) -> pd.Series:
    upper = s.fillna("").astype(str).str.upper()
    return (
        upper.str.startswith("PO BOX")
        | upper.str.match(r"^\d")
        | upper.str.contains(STREET_TYPE_RE)
    ) & ~upper.str.strip().str.match(FLOOR_ONLY_RE)


# replace unusable street with donor's real street
def recover_nonstreet_from_donor(df: pd.DataFrame) -> int:
    usable = is_usable_street(df["contributor_street_1"])
    target = (
        (df["entity_type"] == "INDIVIDUAL")
        & df["contributor_street_1"].notna()
        & (df["contributor_street_1"].astype(str).str.strip() != "")
        & ~usable
    )
    if not target.any():
        return 0

    # never recover a fragment with another
    clean = df.loc[usable & df["contributor_street_1"].notna()]
    if clean.empty:
        return 0
    # keyed by place so street fits
    by_place = (
        clean.groupby(["contributor_name", "contributor_city", "contributor_state"])[
            "contributor_street_1"
        ]
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )
    # fallback only if one street in state
    state_streets: dict[tuple, set] = {}
    for (name, _city, state), street in by_place.items():
        state_streets.setdefault((name, state), set()).add(street)
    by_state_single = {
        key: next(iter(streets))
        for key, streets in state_streets.items()
        if len(streets) == 1
    }

    n_recovered = 0
    for idx in df[target].index:
        name = df.at[idx, "contributor_name"]
        state = df.at[idx, "contributor_state"]
        street = by_place.get(
            (name, df.at[idx, "contributor_city"], state)
        ) or by_state_single.get((name, state))
        if street:
            # keep the unit this filing named
            unit = _unit_text(df.at[idx, "contributor_street_1"])
            df.at[idx, "contributor_street_1"] = street
            second = df.at[idx, "contributor_street_2"] if "contributor_street_2" in df.columns else ""
            if unit and (pd.isna(second) or not str(second).strip()):
                df.at[idx, "contributor_street_2"] = unit
            n_recovered += 1

    return n_recovered


# every unit in text, as KEYWORD NUMBER
def _units_text(text) -> str:
    units = [
        f"# {match.group(3)}" if match.group(3) else f"{'STE' if match.group(1) == 'SUITE' else match.group(1)} {match.group(2)}"
        for match in _UNIT_TEXT_RE.finditer(str(text or "").upper())
    ]
    return " ".join(dict.fromkeys(units))


# return the first unit a text names
def _unit_text(text) -> str:
    units = _units_text(text)
    match = _UNIT_TEXT_RE.search(units)
    return match.group(0) if match else ""


# unit number without its keyword, for comparing
def _unit_key(text) -> str:
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\b(?:APT|STE|SUITE|UNIT|RM)\b|#", "", str(text).upper()))


# fill blank street from unique same-place street
def recover_missing_streets(df: pd.DataFrame) -> int:
    keys = ["donor_key", "contributor_city", "contributor_state", "contributor_zip"]
    street = df["contributor_street_1"].fillna("").astype(str).str.strip()
    is_individual = df["entity_type"] == "INDIVIDUAL"
    context = df[keys].fillna("").astype(str).apply(lambda column: column.str.strip())
    has_context = context.ne("").all(axis=1)

    sources = df.loc[is_individual & has_context & street.ne(""), keys].copy()
    sources["contributor_street_1"] = street[sources.index]
    if sources.empty:
        return 0

    candidates = (
        sources.groupby(keys)["contributor_street_1"]
        .agg(lambda values: values.iloc[0] if values.nunique() == 1 else None)
        .dropna()
        .to_dict()
    )
    recovered = 0
    targets = is_individual & has_context & street.eq("")
    for index in df.index[targets]:
        key = tuple(df.at[index, column] for column in keys)
        value = candidates.get(key)
        if value:
            df.at[index, "contributor_street_1"] = value
            recovered += 1
    return recovered


# FEC cuts street_1 at 34 characters
_TRUNCATION_MIN_LEN = 30
# direction or street type, not a fragment
_NOT_A_FRAGMENT_RE = re.compile(
    r"^(?:N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST|"
    r"ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|EXT)$"
)
# proper unit: keyword plus number, "APT 4B"
_REAL_UNIT_RE = re.compile(r"^(?:APT|STE|SUITE|UNIT|FL|FLOOR|RM|BLDG|PH|PMB|LOT|SPC|BOX|TRLR|#)\s*[A-Z0-9-]+$")
# a unit keyword anywhere in text
_UNIT_IN_FRAGMENT_RE = re.compile(r"(?:^|\s)(?:#|APT|STE|SUITE|UNIT|FL|FLOOR|RM|BLDG|PMB|BOX)\b|#\d")
# a unit keyword and its number
_UNIT_TEXT_RE = re.compile(r"\b(APT|STE|SUITE|UNIT|RM|PMB)\b\s*#?\s*([A-Z0-9][A-Z0-9-]*)\b|#\s*([A-Z0-9][A-Z0-9-]*)\b")
# tail starting a second street address
_SECOND_ADDRESS_RE = re.compile(r"^\d+\s+(?:[NSEW]\s+)?[A-Z]{3,}")


# trim long street to donor's short form
def trim_street_to_donor_short_form(df: pd.DataFrame) -> int:
    name = df["contributor_name"].fillna("").astype(str)
    street = df["contributor_street_1"].fillna("").astype(str).str.strip()
    street_2 = df["contributor_street_2"].fillna("").astype(str).str.strip()
    zip5 = df["contributor_zip"].fillna("").astype(str).str.strip().str[:5]
    individual = (df["entity_type"] == "INDIVIDUAL") & (name != "") & (zip5 != "")

    long_rows = individual & (street.str.len() >= _TRUNCATION_MIN_LEN)
    if not long_rows.any():
        return 0

    # dominant short form per name and ZIP
    short_ok = individual & (street.str.len() < _TRUNCATION_MIN_LEN) & (street.str.len() >= 8) \
        & street.str.match(r"^\d") & is_usable_street(street)
    if not short_ok.any():
        return 0
    short = pd.DataFrame({"name": name[short_ok], "zip5": zip5[short_ok],
                          "street": street[short_ok], "street_2": street_2[short_ok]})
    counts = short.groupby(["name", "zip5", "street"]).size().rename("cnt").reset_index()
    dominant = counts.loc[counts.groupby(["name", "zip5"])["cnt"].idxmax()]
    # trust unit filed on at least half
    unit_like = short["street_2"].str.match(_REAL_UNIT_RE) & ~short["street_2"].map(is_state_zip_fragment)
    with_unit = short[(short["street_2"] != "") & unit_like]
    unit_counts = with_unit.groupby(["name", "zip5", "street"])["street_2"].agg(lambda s: s.value_counts().iloc[0])
    unit_value = with_unit.groupby(["name", "zip5", "street"])["street_2"].agg(lambda s: s.value_counts().index[0])
    units = pd.DataFrame({"unit": unit_value, "unit_cnt": unit_counts}).reset_index()
    dominant = dominant.merge(units, on=["name", "zip5", "street"], how="left")
    dominant["unit"] = dominant["unit"].where(dominant["unit_cnt"] * 2 >= dominant["cnt"], "").fillna("")
    lookup = dominant.set_index(["name", "zip5"])[["street", "unit"]]

    n_fixed = 0
    for idx in df.index[long_rows]:
        key = (name.at[idx], zip5.at[idx])
        if key not in lookup.index:
            continue
        short_street, unit = lookup.loc[key, "street"], lookup.loc[key, "unit"]
        long_street = street.at[idx]
        if not (long_street.startswith(short_street + " ") or long_street.startswith(short_street + ",")):
            continue
        fragment = long_street[len(short_street):].strip(" ,.")
        if len(fragment) < 3 or _NOT_A_FRAGMENT_RE.match(fragment.split()[0]):
            continue  # tail is part of the street
        if _SECOND_ADDRESS_RE.match(fragment) and fragment.split()[0] != short_street.split()[0]:
            continue  # a second address, not a cut-off
        own_units = _units_text(fragment)
        if own_units:
            # keep filing's own units, never donor's usual
            if street_2.at[idx] and _unit_key(street_2.at[idx]) != _unit_key(own_units):
                continue
            df.at[idx, "contributor_street_1"] = short_street
            df.at[idx, "contributor_street_2"] = street_2.at[idx] or own_units
            n_fixed += 1
            continue
        df.at[idx, "contributor_street_1"] = short_street
        if not street_2.at[idx] and unit:
            df.at[idx, "contributor_street_2"] = unit
        n_fixed += 1
    return n_fixed
