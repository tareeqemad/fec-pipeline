"""Read and evaluate the reviewed donor identity rules (donor_identity_rules.csv).

merge_keys    two donor keys are one person (or one committee, with a canonical name)
merge_names   every filing whose name starts with name_a is one person
separate      two names, optionally at a place, are different people
hold          one filing belongs to no confirmed donor
hold_address  a name at a street and ZIP belongs to no confirmed donor
"""

import re

import pandas as pd

from fec.common.files import read_rows
from fec.common.settings import RULES_DIR
from fec.common.text import upper_single_spaced

RULES_PATH = RULES_DIR / "donor_identity_rules.csv"
REQUIRED_FIELDS = {
    "merge_keys": ("donor_key_a", "donor_key_b"),
    "merge_names": ("name_a",),
    "separate": ("name_a", "name_b"),
    "hold": ("sub_id", "group"),
    "hold_address": ("name_a", "street_a", "zip_a", "group"),
}
VALID_REVIEW_STATUSES = {"verified_fec", "verified_web"}


def _build_identity(name: str, city: str, state: str, zip5: str = "") -> str:
    parts = (name, city, state, zip5) if zip5 else (name, city, state)
    return "|".join(map(upper_single_spaced, parts))


def _is_valid_rule(action: str, row: dict) -> bool:
    review_status = (row.get("review_status") or "").strip().lower()
    reviewed_at = (row.get("reviewed_at") or "").strip()
    return (
        action in REQUIRED_FIELDS
        and review_status in VALID_REVIEW_STATUSES
        and bool((row.get("source") or "").strip())
        and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed_at))
        and all(row.get(field) for field in REQUIRED_FIELDS[action])
    )


def _read_identity_rules() -> dict[str, list[dict]]:
    by_action = {action: [] for action in REQUIRED_FIELDS}
    for number, row in enumerate(read_rows(RULES_PATH), start=2):
        action = (row.get("action") or "").strip().lower()
        if not _is_valid_rule(action, row):
            raise ValueError(
                f"Invalid identity rule on row {number}: it needs a known action, "
                "its fields, a review status, a source and a YYYY-MM-DD reviewed_at"
            )
        by_action[action].append(row)
    return by_action


def _load_separations(rows: list[dict]) -> tuple[set[frozenset[str]], set[frozenset[str]], frozenset[str]]:
    separations = [
        (
            upper_single_spaced(row["name_a"]),
            upper_single_spaced(row["name_b"]),
            (upper_single_spaced(row.get("city_a")), upper_single_spaced(row.get("state_a"))),
            (upper_single_spaced(row.get("city_b")), upper_single_spaced(row.get("state_b"))),
            (upper_single_spaced(row.get("zip_a"))[:5], upper_single_spaced(row.get("zip_b"))[:5]),
        )
        for row in rows
    ]
    # ZIPs matter only when the city cannot tell the names apart.
    zip_names = {
        name
        for name_a, name_b, place_a, place_b, zips in separations
        if all(place_a + place_b + zips) and place_a == place_b
        for name in (name_a, name_b)
    }
    name_pairs, identity_pairs = set(), set()
    for name_a, name_b, place_a, place_b, zips in separations:
        if not all(place_a + place_b):
            name_pairs.add(frozenset((name_a, name_b)))
            continue
        zip_a, zip_b = zips if all(zips) and {name_a, name_b} <= zip_names else ("", "")
        identity_a = _build_identity(name_a, *place_a, zip_a)
        identity_b = _build_identity(name_b, *place_b, zip_b)
        identity_pairs.add(frozenset((identity_a, identity_b)))
    return name_pairs, identity_pairs, frozenset(zip_names)


_RULES = _read_identity_rules()
SEPARATE_NAMES, SEPARATE_IDENTITIES, ZIP_SPLIT_NAMES = _load_separations(_RULES["separate"])
# donor key to drop -> key to keep; a rule scoped to sub_ids renames a committee only.
KEY_MERGES = {
    row["donor_key_b"].strip(): row["donor_key_a"].strip()
    for row in _RULES["merge_keys"]
    if not row.get("sub_ids") and row["donor_key_a"].strip() != row["donor_key_b"].strip()
}
COMMITTEE_NAMES = tuple(
    (row["donor_key_a"], row["donor_key_b"], row["canonical_name"], frozenset((row.get("sub_ids") or "").split()))
    for row in _RULES["merge_keys"]
    if row.get("canonical_name")
)
# sub_id -> hold group, and (name, street, zip5) -> hold group for later filings too.
HELD_FILINGS = {row["sub_id"].strip(): row["group"].strip() for row in _RULES["hold"]}
HELD_ADDRESSES = {
    (
        upper_single_spaced(row["name_a"]),
        upper_single_spaced(row["street_a"]),
        upper_single_spaced(row["zip_a"])[:5],
    ): row["group"].strip()
    for row in _RULES["hold_address"]
}
# group -> the name prefixes it joins, in file order.
NAME_MERGES = {}
for _row in _RULES["merge_names"]:
    _name = upper_single_spaced(_row["name_a"])
    _group = (_row.get("group") or _name).strip()
    NAME_MERGES[_group] = NAME_MERGES.get(_group, ()) + (_name,)


def held_groups(df: pd.DataFrame) -> pd.Series:
    """Return a hold rule's group per row: by sub_id, else by name, street and ZIP."""
    groups = df["sub_id"].astype(str).str.strip().map(HELD_FILINGS).fillna("")
    places = zip(
        df["contributor_name"].map(upper_single_spaced),
        df["contributor_street_1"].map(upper_single_spaced),
        df["contributor_zip"].map(upper_single_spaced).str[:5],
    )
    by_place = pd.Series([HELD_ADDRESSES.get(place, "") for place in places], index=df.index)
    return groups.where(groups != "", by_place)


def split_zip(name: str, zip_code: str) -> str:
    """Return the ZIP5 also keying this name, for ZIP-split names only."""
    return upper_single_spaced(zip_code)[:5] if upper_single_spaced(name) in ZIP_SPLIT_NAMES else ""


def identities_must_stay_separate(person_a: dict, person_b: dict) -> bool:
    """Tell whether curated rules require these two donors stay separate."""
    if frozenset((upper_single_spaced(person_a["name"]), upper_single_spaced(person_b["name"]))) in SEPARATE_NAMES:
        return True
    place_a = (person_a["name"], person_a["city"], person_a["state"])
    place_b = (person_b["name"], person_b["city"], person_b["state"])
    pair = frozenset((_build_identity(*place_a), _build_identity(*place_b)))
    zip_a = _build_identity(*place_a, person_a.get("zip5", "")[:5])
    zip_b = _build_identity(*place_b, person_b.get("zip5", "")[:5])
    return pair in SEPARATE_IDENTITIES or frozenset((zip_a, zip_b)) in SEPARATE_IDENTITIES


def resolve_donor_key(key: str) -> str:
    """Follow verified merge chains to the surviving key."""
    seen = set()
    while key in KEY_MERGES and key not in seen:
        seen.add(key)
        key = KEY_MERGES[key]
    return key
