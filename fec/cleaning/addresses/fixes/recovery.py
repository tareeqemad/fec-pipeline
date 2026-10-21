"""Recover a donor's missing or unusable address fields from their own other filings."""

from __future__ import annotations

import re

import pandas as pd

from fec.cleaning.addresses.fixes.safe_text import is_state_zip_fragment
from fec.config.streets import FLOOR_ONLY_RE

# a street_1 is usable if a geocoder can place it: house number, PO box, or a
# street-type token. deliberately NARROWER than address_review._STREET_TYPES:
# this runs on POST-normalized streets (types already abbreviated) and unit
# keywords (APT/STE) don't make a street usable. never merge the two lists.
_STREET_TYPE_RE = re.compile(
    r"\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|"
    r"ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|"
    r"RUN|PT|PASS|WALK|BEND|MALL)\b"
)


def _text(value) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _identity_column(df: pd.DataFrame) -> str:
    return "donor_key" if "donor_key" in df.columns else "contributor_name"


def _source_addresses(df: pd.DataFrame, keys: list[str]) -> dict:
    """All usable individual addresses, without selecting a modal street."""
    usable = _is_usable_street(df["contributor_street_1"])
    if "entity_type" in df.columns:
        usable &= df["entity_type"].eq("INDIVIDUAL")
    sources = {}
    columns = keys + ["contributor_street_1"]
    if "contributor_street_2" in df:
        columns.append("contributor_street_2")
    for row in df.loc[usable, columns].drop_duplicates().to_dict("records"):
        key = tuple(_text(row[column]) for column in keys)
        if all(key):
            sources.setdefault(key, set()).add((
                _text(row["contributor_street_1"]),
                _unit_key(_text(row.get("contributor_street_2"))),
            ))
    return sources


def _unique_street(addresses, unit: str = "") -> str | None:
    """A filed unit must match a source unit; missing source units prove nothing."""
    streets = {street for street, source_unit in addresses if not unit or source_unit == _unit_key(unit)}
    return next(iter(streets)) if len(streets) == 1 else None


def _fill_blank_streets(df: pd.DataFrame, targets: pd.Series, keys: list[str]) -> int:
    sources = _source_addresses(df, keys)
    if "entity_type" in df.columns:
        targets &= df["entity_type"].eq("INDIVIDUAL")
    if "_care_of_blanked" in df.columns:
        targets &= ~df["_care_of_blanked"].fillna(False).astype(bool)
    changed = 0
    for index in df.index[targets]:
        key = tuple(_text(df.at[index, column]) for column in keys)
        unit = _text(df.at[index, "contributor_street_2"]) if "contributor_street_2" in df else ""
        street = _unique_street(sources.get(key, ()), unit)
        if street:
            df.at[index, "contributor_street_1"] = street
            changed += 1
    return changed


def _recover_null_streets(df: pd.DataFrame) -> int:
    """Fill a missing street only from an unambiguous, unit-compatible address."""
    targets = df["contributor_street_1"].isna()
    if not targets.any():
        return 0
    keys = [_identity_column(df), "contributor_city", "contributor_state"]
    if "contributor_zip" in df:
        keys.append("contributor_zip")
    return _fill_blank_streets(df, targets, keys)


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
    """Complete an actual street fragment; preserve institution and place names."""
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

    keys = [_identity_column(df), "contributor_city", "contributor_state"]
    if "contributor_zip" in df:
        keys.append("contributor_zip")
    sources = _source_addresses(df, keys)
    n_recovered = 0
    for idx in df[target].index:
        text = _text(df.at[idx, "contributor_street_1"])
        key = tuple(_text(df.at[idx, column]) for column in keys)
        candidates = sources.get(key, ())
        unit = _unit_text(text)
        standalone_unit = bool(_REAL_UNIT_RE.fullmatch(text) or re.fullmatch(FLOOR_ONLY_RE, text))
        if standalone_unit and not unit:
            unit = text
        second = _text(df.at[idx, "contributor_street_2"]) if "contributor_street_2" in df else ""
        if unit and second and _unit_key(unit) != _unit_key(second):
            continue
        # A building's own numbered street fragment is evidence even when its
        # unit differs from another filing at that building (RING HOUSE ...).
        matched = [(street, source_unit) for street, source_unit in candidates
                   if _contains_street_fragment(text, street)]
        street = _unique_street(matched)
        if not street and standalone_unit:
            street = _unique_street(candidates, second or unit)
        if street:
            df.at[idx, "contributor_street_1"] = street
            if unit and not second:
                df.at[idx, "contributor_street_2"] = unit
            n_recovered += 1

    return n_recovered


def _contains_street_fragment(text: str, street: str) -> bool:
    """Require a house number and street-name text already present in the filing."""
    text = _UNIT_TEXT_RE.sub("", text)
    for match in re.finditer(r"\b\d+[A-Z]?\s+[A-Z]", text):
        fragment = text[match.start():].strip(" ,.")
        words = fragment.split()[1:]
        if any(len(word) >= 2 and word not in {"NE", "NW", "SE", "SW"} for word in words):
            if street.startswith(fragment):
                return True
    return False


# every unit a text names, as KEYWORD NUMBER: "STE 103 PMB 132", "#374 LOS ANGELES" -> "# 374"
def _units_text(text) -> str:
    units = [
        f"# {match.group(3)}" if match.group(3) else f"{'STE' if match.group(1) == 'SUITE' else match.group(1)} {match.group(2)}"
        for match in _UNIT_TEXT_RE.finditer(str(text or "").upper())
    ]
    return " ".join(dict.fromkeys(units))


# the first unit a text names
def _unit_text(text) -> str:
    units = _units_text(text)
    match = _UNIT_TEXT_RE.search(units)
    return match.group(0) if match else ""


# a unit's numbers and letters without its keywords: "STE 374" and "# 374" -> "374"
def _unit_key(text) -> str:
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\b(?:APT|STE|SUITE|UNIT|RM)\b|#", "", str(text).upper()))


# fill a blank street from the donor's one matching street
def _recover_missing_streets(df: pd.DataFrame) -> int:
    """Fill a blank street from the donor's only street in the same place."""
    keys = ["donor_key", "contributor_city", "contributor_state", "contributor_zip"]
    street = df["contributor_street_1"].fillna("").astype(str).str.strip()
    return _fill_blank_streets(df, street.eq(""), keys)


# FEC cuts street_1 at 34 characters, so a filer who typed the whole address
# into one box leaves "1777 REISTERSTOWN RD COMMERCE CE" or
# "1474 BIENVENEDA AVE PACIFIC PALIS": the real street followed by a building,
# city or state fragment. The same person's other filings at the same ZIP hold
# the clean short form (and its unit in street_2), which is the evidence used.
_TRUNCATION_MIN_LEN = 30
# a direction or street-type word, whole: "AVE", "NE" - not a stray tail fragment
_NOT_A_FRAGMENT_RE = re.compile(
    r"^(?:N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST|"
    r"ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|EXT)$"
)
# a proper unit value: keyword plus number/letters, e.g. "APT 4B", "STE 200"
_REAL_UNIT_RE = re.compile(r"^(?:APT|STE|SUITE|UNIT|FL|FLOOR|RM|BLDG|PH|PMB|LOT|SPC|BOX|TRLR|#)\s*[A-Z0-9-]+$")
# the first unit in a text, keyword and number: "RING HOUSE APT # 431 1801 E JE" -> "APT 431"
_UNIT_TEXT_RE = re.compile(r"\b(APT|STE|SUITE|UNIT|RM|PMB)\b\s*#?\s*([A-Z0-9][A-Z0-9-]*)\b|#\s*([A-Z0-9][A-Z0-9-]*)\b")
# a tail that starts a new street address: house number, then a name word ("233 E BAY")
_SECOND_ADDRESS_RE = re.compile(r"^\d+\s+(?:[NSEW]\s+)?[A-Z]{3,}")


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
    units = []
    for key, values in with_unit.groupby(["name", "zip5", "street"])["street_2"]:
        frequencies = values.value_counts()
        units.append((*key, frequencies.index[0], frequencies.iloc[0]))
    units = pd.DataFrame(units, columns=["name", "zip5", "street", "unit", "unit_cnt"])
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
        if _SECOND_ADDRESS_RE.match(fragment) and fragment.split()[0] != short_street.split()[0]:
            continue  # "1125 BLACKSTONE BLDG 233 E BAY": a second address, not a cut-off copy
        own_units = _units_text(fragment)
        if own_units:
            # the filing's own units ("STE 103 PMB 132") go to street_2 and the
            # city tail is dropped; never the donor's usual unit in their place.
            # A different unit already in street_2 leaves the row as filed.
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
