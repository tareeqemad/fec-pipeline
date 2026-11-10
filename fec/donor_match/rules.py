"""Read and evaluate the reviewed donor identity rules (donor_identity_rules.csv).

merge_keys    two donor keys are one person (or one committee, with a canonical name)
merge_names   every filing whose name starts with name_a is one person
separate      two names, optionally at a place, are different people
hold          one filing belongs to no confirmed donor
hold_address  a name at a street and ZIP belongs to no confirmed donor
"""

import csv
import re
from collections import defaultdict
from typing import TYPE_CHECKING

from fec.env import RULES_DIR

if TYPE_CHECKING:
    import pandas as pd

RULES_PATH = RULES_DIR / "donor_identity_rules.csv"
REQUIRED_FIELDS = {
    "merge_keys": ("donor_key_a", "donor_key_b"),
    "merge_names": ("name_a",),
    "separate": ("name_a", "name_b"),
    "hold": ("sub_id", "group"),
    "hold_address": ("name_a", "street_a", "zip_a", "group"),
}
VALID_REVIEW_STATUSES = {"verified_fec", "verified_web"}


def _normalize(value: str) -> str:
    """Uppercase with single spaces."""
    return " ".join(str(value or "").upper().split())


def _identity(name: str, city: str, state: str, zip5: str = "") -> str:
    """Key a name at a place, with the ZIP when the rule is that narrow."""
    parts = (name, city, state, zip5) if zip5 else (name, city, state)
    return "|".join(map(_normalize, parts))


def _read_rules() -> dict[str, list[dict]]:
    """Read the rule rows by action; every row needs its fields, a source and a review date."""
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"Missing donor identity rules: {RULES_PATH}")
    by_action = {action: [] for action in REQUIRED_FIELDS}
    with RULES_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            action = (row.get("action") or "").strip().lower()
            if (
                action not in REQUIRED_FIELDS
                or (row.get("review_status") or "").strip().lower() not in VALID_REVIEW_STATUSES
                or not (row.get("source") or "").strip()
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (row.get("reviewed_at") or "").strip())
                or not all(row.get(field) for field in REQUIRED_FIELDS[action])
            ):
                raise ValueError(
                    f"Invalid identity rule on row {number}: it needs a known action, "
                    "its fields, a review status, a source and a YYYY-MM-DD reviewed_at"
                )
            by_action[action].append(row)
    return by_action


def _load_separations(
    rows: list[dict],
) -> tuple[set[frozenset[str]], set[frozenset[str]], frozenset[str]]:
    """Name pairs, name-at-place pairs, and the names only a ZIP can tell apart."""
    separations = [
        (
            _normalize(row["name_a"]),
            _normalize(row["name_b"]),
            (_normalize(row.get("city_a")), _normalize(row.get("state_a"))),
            (_normalize(row.get("city_b")), _normalize(row.get("state_b"))),
            (_normalize(row.get("zip_a"))[:5], _normalize(row.get("zip_b"))[:5]),
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
        by_zip = all(zips) and {name_a, name_b} <= zip_names
        identity_pairs.add(
            frozenset(
                (
                    _identity(name_a, *place_a, zips[0] if by_zip else ""),
                    _identity(name_b, *place_b, zips[1] if by_zip else ""),
                )
            )
        )
    return name_pairs, identity_pairs, frozenset(zip_names)


_RULES = _read_rules()
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
    (_normalize(row["name_a"]), _normalize(row["street_a"]), _normalize(row["zip_a"])[:5]): row["group"].strip()
    for row in _RULES["hold_address"]
}
NAME_MERGES = defaultdict(tuple)
for _row in _RULES["merge_names"]:
    _name = _normalize(_row["name_a"])
    NAME_MERGES[(_row.get("group") or _name).strip()] += (_name,)
NAME_MERGES = dict(NAME_MERGES)


def held_groups(
    df: "pd.DataFrame",
    filings: dict[str, str] | None = None,
    addresses: dict[tuple[str, str, str], str] | None = None,
) -> "pd.Series":
    """Return a hold rule's group per row: by sub_id, else address."""
    import pandas as pd

    filings = HELD_FILINGS if filings is None else filings
    addresses = HELD_ADDRESSES if addresses is None else addresses

    groups = pd.Series("", index=df.index, dtype=object)
    if filings:
        groups = df["sub_id"].astype(str).str.strip().map(filings).fillna("")
    if addresses:
        place = list(
            zip(
                df["contributor_name"].map(_normalize),
                df["contributor_street_1"].map(_normalize),
                df["contributor_zip"].map(_normalize).str[:5],
            )
        )
        by_place = pd.Series([addresses.get(key, "") for key in place], index=df.index)
        groups = groups.where(groups != "", by_place)
    return groups


def names_must_stay_separate(name_a: str, name_b: str) -> bool:
    """Tell whether this name pair is a curated separation rule."""
    pair = frozenset((_normalize(name_a), _normalize(name_b)))
    return pair in SEPARATE_NAMES


def split_zip(name: str, zip_code: str) -> str:
    """Return the ZIP5 also keying this name, for ZIP-split names only."""
    return _normalize(zip_code)[:5] if _normalize(name) in ZIP_SPLIT_NAMES else ""


def identities_must_stay_separate(person_a: dict, person_b: dict) -> bool:
    """Tell whether curated rules require these two donors stay separate."""
    if names_must_stay_separate(person_a["name"], person_b["name"]):
        return True
    pair = frozenset(
        (
            _identity(person_a["name"], person_a["city"], person_a["state"]),
            _identity(person_b["name"], person_b["city"], person_b["state"]),
        )
    )
    zip_pair = frozenset(
        (
            _identity(person_a["name"], person_a["city"], person_a["state"], person_a.get("zip5", "")[:5]),
            _identity(person_b["name"], person_b["city"], person_b["state"], person_b.get("zip5", "")[:5]),
        )
    )
    return pair in SEPARATE_IDENTITIES or zip_pair in SEPARATE_IDENTITIES


def resolve_donor_key(key: str) -> str:
    """Follow verified merge chains to the surviving key."""
    seen = set()
    while key in KEY_MERGES and key not in seen:
        seen.add(key)
        key = KEY_MERGES[key]
    return key
