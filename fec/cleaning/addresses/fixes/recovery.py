"""Trim address tails without copying missing address details."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.streets import FLOOR_ONLY_RE

_STREET_TYPE_RE = re.compile(
    r"\b(?:ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|PLAZA|"
    r"ROW|LOOP|BROADWAY|PARK|BLDG|TPKE|EXPY|HTS|RIDGE|XING|PIKE|RTE|ALY|PATH|"
    r"RUN|PT|PASS|WALK|BEND|MALL)\b"
)
_TRUNCATION_MIN_LEN = 30
_NOT_A_FRAGMENT_RE = re.compile(
    r"^(?:N|S|E|W|NE|NW|SE|SW|NORTH|SOUTH|EAST|WEST|"
    r"ST|AVE|RD|BLVD|DR|LN|CT|CIR|PL|PKWY|HWY|TER|SQ|WAY|TRL|PLZ|EXT)$"
)
_UNIT_TEXT_RE = re.compile(r"\b(APT|STE|SUITE|UNIT|RM|PMB)\b\s*#?\s*([A-Z0-9][A-Z0-9-]*)\b|#\s*([A-Z0-9][A-Z0-9-]*)\b")
_SECOND_ADDRESS_RE = re.compile(r"^\d+\s+(?:[NSEW]\s+)?[A-Z]{3,}")


# A floor alone is a unit, not a usable street.
def _is_usable_street(s: pd.Series) -> pd.Series:
    upper = s.fillna("").astype(str).str.upper()
    return (
        upper.str.startswith("PO BOX")
        | upper.str.match(r"^\d")
        | upper.str.contains(_STREET_TYPE_RE)
    ) & ~upper.str.strip().str.match(FLOOR_ONLY_RE)


# Keep all units actually written in this address.
def _units_text(text) -> str:
    units = [
        f"# {match.group(3)}" if match.group(3) else f"{'STE' if match.group(1) == 'SUITE' else match.group(1)} {match.group(2)}"
        for match in _UNIT_TEXT_RE.finditer(str(text or "").upper())
    ]
    return " ".join(dict.fromkeys(units))


# Compare unit values without their labels.
def _unit_key(text) -> str:
    return re.sub(r"[^A-Z0-9]", "", re.sub(r"\b(?:APT|STE|SUITE|UNIT|RM)\b|#", "", str(text).upper()))


# Keep an existing full street prefix; never copy missing units.
def _trim_street_to_donor_short_form(df: pd.DataFrame) -> int:
    name = df["contributor_name"].fillna("").astype(str)
    street = df["contributor_street_1"].fillna("").astype(str).str.strip()
    street_2 = df["contributor_street_2"].fillna("").astype(str).str.strip()
    zip5 = df["contributor_zip"].fillna("").astype(str).str.strip().str[:5]
    individual = df["entity_type"].eq("INDIVIDUAL") & name.ne("") & zip5.ne("")
    long_rows = individual & street.str.len().ge(_TRUNCATION_MIN_LEN)
    if not long_rows.any():
        return 0

    short_ok = individual & street.str.len().between(8, _TRUNCATION_MIN_LEN - 1) \
        & street.str.match(r"^\d") & _is_usable_street(street)
    short_forms = {}
    for idx in df.index[short_ok]:
        short_forms.setdefault((name.at[idx], zip5.at[idx]), set()).add(street.at[idx])

    changed = 0
    for idx in df.index[long_rows]:
        long_street = street.at[idx]
        matches = []
        for short_street in short_forms.get((name.at[idx], zip5.at[idx]), ()):
            if not long_street.startswith((short_street + " ", short_street + ",")):
                continue
            fragment = long_street[len(short_street):].strip(" ,.")
            if len(fragment) < 3 or _NOT_A_FRAGMENT_RE.match(fragment.split()[0]):
                continue
            if _SECOND_ADDRESS_RE.match(fragment) and fragment.split()[0] != short_street.split()[0]:
                continue
            own_units = _units_text(fragment)
            if own_units and street_2.at[idx] and _unit_key(street_2.at[idx]) != _unit_key(own_units):
                continue
            matches.append((short_street, own_units))
        if len(matches) != 1:
            continue
        short_street, own_units = matches[0]
        df.at[idx, "contributor_street_1"] = short_street
        if own_units and not street_2.at[idx]:
            df.at[idx, "contributor_street_2"] = own_units
        changed += 1
    return changed
