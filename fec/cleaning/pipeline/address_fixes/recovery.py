"""Recover a donor's missing or unusable address fields from their own other filings."""

from __future__ import annotations

import re

import pandas as pd

from fec.config.streets import FLOOR_ONLY_RE

from .safe_text import is_state_zip_fragment

# a street_1 is usable if a geocoder can place it: house number, PO box, or a
# street-type token. deliberately NARROWER than address_review._STREET_TYPES:
# this runs on POST-normalized streets (types already abbreviated) and unit
# keywords (APT/STE) don't make a street usable. never merge the two lists.
_STREET_TYPE_RE = re.compile(
    r"\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|"
    r"ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|"
    r"RUN|PT|PASS|WALK|BEND|MALL)\b"
)


# fill null streets from rows with same name/city/state
def _recover_null_streets(df: pd.DataFrame) -> int:
    """Fill NULL streets (e.g. nulled emails) from other records of the same (name, city, state)."""
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
            df.at[idx, "contributor_street_1"] = street
            n_recovered += 1

    return n_recovered


# check whether street_1 values look geocodable
def _is_usable_street(s: pd.Series) -> pd.Series:
    """Vectorised: True where street_1 looks geocodable; a floor alone ("3RD FLOOR") is a unit, not a street."""
    upper = s.fillna("").astype(str).str.upper()
    return (
        upper.str.startswith("PO BOX")
        | upper.str.match(r"^\d")
        | upper.str.contains(_STREET_TYPE_RE)
    ) & ~upper.str.strip().str.match(FLOOR_ONLY_RE)


# replace an unusable street with the donor's real street
def _recover_nonstreet_from_donor(df: pd.DataFrame) -> int:
    """Replace a non-usable street_1 (place-name / fragment) with the same donor's real street."""
    # individuals only; only a non-usable value is overwritten, a good street never
    usable = _is_usable_street(df["contributor_street_1"])
    target = (
        (df["entity_type"] == "INDIVIDUAL")
        & df["contributor_street_1"].notna()
        & (df["contributor_street_1"].astype(str).str.strip() != "")
        & ~usable
    )
    if not target.any():
        return 0

    # lookup uses ONLY usable streets - never recover one fragment with another
    clean = df.loc[usable & df["contributor_street_1"].notna()]
    if clean.empty:
        return 0
    # primary key: name + city + state, so the street matches the row's place
    by_place = (
        clean.groupby(["contributor_name", "contributor_city", "contributor_state"])[
            "contributor_street_1"
        ]
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )
    # fallback name + state only when the donor has exactly ONE clean street in
    # the whole state (no city ambiguity, so a garbled city on the fragment row
    # is safe to look past); two distinct streets -> skip, can't choose
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
            df.at[idx, "contributor_street_1"] = street
            n_recovered += 1

    return n_recovered


# backfill a missing house number from the donor's numbered filing
def _recover_house_number_from_donor(df: pd.DataFrame) -> int:
    """Backfill a missing house number from the same donor's numbered filing of the same street."""
    # a typed-but-unnumbered street ("FAIRWAY DR") passes _is_usable_street yet
    # only geocodes to a centroid. conservative: the number-stripped street must
    # match EXACTLY, key is name+state, only the dominant numbered form is used.
    streets = df["contributor_street_1"].fillna("").astype(str).str.upper().str.strip()
    is_indiv = df["entity_type"] == "INDIVIDUAL"
    has_type = streets.str.contains(_STREET_TYPE_RE)
    starts_num = streets.str.match(r"^\d")
    no_number = (
        is_indiv
        & has_type
        & ~starts_num
        & ~streets.str.startswith("PO BOX")
        & (streets != "")
    )
    if not no_number.any():
        return 0

    numbered = df[is_indiv & starts_num]
    if numbered.empty:
        return 0

    # drop a leading house number from a street string
    def _strip_num(x: str) -> str:
        return re.sub(r"^\d+\s+", "", str(x).upper().strip())

    numbered_streets = numbered[
        ["contributor_name", "contributor_state", "contributor_street_1"]
    ].copy()
    numbered_streets["_stripped"] = numbered_streets["contributor_street_1"].map(
        _strip_num
    )
    key_full = (
        numbered_streets.groupby(
            ["contributor_name", "contributor_state", "_stripped"]
        )["contributor_street_1"]
        .agg(lambda values: values.value_counts().index[0])
        .to_dict()
    )

    n_filled = 0
    for idx in df[no_number].index:
        full = key_full.get(
            (
                df.at[idx, "contributor_name"],
                df.at[idx, "contributor_state"],
                streets.at[idx],
            )
        )
        if full and str(full).upper().strip() != streets.at[idx]:
            df.at[idx, "contributor_street_1"] = full
            n_filled += 1
    return n_filled


# fill a blank street from the donor's one matching street
def _recover_missing_streets(df: pd.DataFrame) -> int:
    """Fill a blank street from the donor's only street in the same place."""
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


# decide if one house number is a truncation of another
def _house_number_replacement(first, second, counts) -> tuple | None:
    first_parts = str(first).split(" ", 1)
    second_parts = str(second).split(" ", 1)
    if len(first_parts) < 2 or len(second_parts) < 2:
        return None
    if first_parts[1] != second_parts[1]:
        return None

    first_number, second_number = first_parts[0], second_parts[0]
    if not first_number.isdigit() or not second_number.isdigit():
        return None
    if not (
        first_number.startswith(second_number) or second_number.startswith(first_number)
    ):
        return None

    if counts[first] <= 2 and counts[second] >= 5:
        return first, second
    if counts[second] <= 2 and counts[first] >= 5:
        return second, first
    return None


# replace a rare truncated house number with donor's common form
def _truncated_house_numbers(df: pd.DataFrame) -> int:
    """Replace a rare truncated house number with the donor's common form."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    changed = 0
    for donor_key, group in individuals.groupby("donor_key"):
        streets = group["contributor_street_1"].dropna().value_counts()
        for first in streets.index:
            for second in streets.index:
                if first >= second:
                    continue
                replacement = _house_number_replacement(first, second, streets)
                if replacement is None:
                    continue
                old, new = replacement
                mask = (df["donor_key"] == donor_key) & (
                    df["contributor_street_1"] == old
                )
                df.loc[mask, "contributor_street_1"] = new
                changed += int(mask.sum())
    return changed


# FEC cuts street_1 at 34 characters, so a filer who typed the whole address
# into one box leaves "1777 REISTERSTOWN RD COMMERCE CE" or
# "1474 BIENVENEDA AVE PACIFIC PALIS": the real street followed by a building,
# city or state fragment. The same person's other filings at the same ZIP hold
# the clean short form (and its unit in street_2), which is the evidence used.
_TRUNCATION_MIN_LEN = 30
_NOT_A_FRAGMENT_RE = re.compile(
    r"^(?:N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST|"
    r"ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|EXT)$"
)
_REAL_UNIT_RE = re.compile(r"^(?:APT|STE|SUITE|UNIT|FL|FLOOR|RM|BLDG|PH|PMB|LOT|SPC|BOX|TRLR|#)\s*[A-Z0-9-]+$")
_UNIT_IN_FRAGMENT_RE = re.compile(r"(?:^|\s)(?:#|APT|STE|SUITE|UNIT|FL|FLOOR|RM|BLDG)\b|#\d")


# trim a long street to donor's shorter same-ZIP form
def _trim_street_to_donor_short_form(df: pd.DataFrame) -> int:
    """Replace a long street_1 that extends a shorter street the same donor filed at the same ZIP."""
    name = df["contributor_name"].fillna("").astype(str)
    street = df["contributor_street_1"].fillna("").astype(str).str.strip()
    street_2 = df["contributor_street_2"].fillna("").astype(str).str.strip()
    zip5 = df["contributor_zip"].fillna("").astype(str).str.strip().str[:5]
    individual = (df["entity_type"] == "INDIVIDUAL") & (name != "") & (zip5 != "")

    long_rows = individual & (street.str.len() >= _TRUNCATION_MIN_LEN)
    if not long_rows.any():
        return 0

    # dominant short form per (name, zip): a usable, house-numbered street shorter than the cutoff
    short_ok = individual & (street.str.len() < _TRUNCATION_MIN_LEN) & (street.str.len() >= 8) \
        & street.str.match(r"^\d") & _is_usable_street(street)
    if not short_ok.any():
        return 0
    short = pd.DataFrame({"name": name[short_ok], "zip5": zip5[short_ok],
                          "street": street[short_ok], "street_2": street_2[short_ok]})
    counts = short.groupby(["name", "zip5", "street"]).size().rename("cnt").reset_index()
    dominant = counts.loc[counts.groupby(["name", "zip5"])["cnt"].idxmax()]
    # the unit is trusted only when it is filed on at least half of the short-form rows
    # and looks like a unit (not a "# NY1179" state/ZIP fragment left by a bad split)
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
            continue  # "4715 CAMBRIDGE APPROACH CIR NE": the tail is part of the street, not a fragment
        if _UNIT_IN_FRAGMENT_RE.search(fragment) and not unit and not street_2.at[idx]:
            continue  # the fragment carries a unit nobody else filed: keep it rather than lose it
        df.at[idx, "contributor_street_1"] = short_street
        if not street_2.at[idx] and unit:
            df.at[idx, "contributor_street_2"] = unit
        n_fixed += 1
    return n_fixed
