"""Load curated donor identity rules from CSV."""

import csv
import re
from collections import defaultdict

from fec.env import RULES_DIR


RULES_PATH = RULES_DIR / "donor_identity_rules.csv"
VALID_ACTIONS = {"merge_keys", "merge_names", "separate", "hold", "hold_address"}
VALID_REVIEW_STATUSES = {"verified_fec", "verified_web"}
REQUIRED_FIELDS = {
    "merge_keys": ("donor_key_a", "donor_key_b"),
    "merge_names": ("name_a",),
    "separate": ("name_a", "name_b"),
    "hold": ("sub_id", "group"),
    "hold_address": ("name_a", "street_a", "zip_a", "group"),
}


# uppercase and collapse whitespace
def _normalize(value: str) -> str:
    return " ".join(str(value or "").upper().split())


# build a name+place identity key
def _identity(name: str, city: str, state: str, zip5: str = "") -> str:
    parts = (name, city, state, zip5) if zip5 else (name, city, state)
    return "|".join(map(_normalize, parts))


# read and validate identity rules from CSV
def _read_rules() -> list[dict[str, str]]:
    if not RULES_PATH.exists():
        raise FileNotFoundError(f"Missing donor identity rules: {RULES_PATH}")
    with RULES_PATH.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    for number, row in enumerate(rows, start=2):
        action = (row.get("action") or "").strip().lower()
        if action not in VALID_ACTIONS:
            raise ValueError(f"Invalid identity action on row {number}: {action}")

        status = (row.get("review_status") or "").strip().lower()
        if status not in VALID_REVIEW_STATUSES:
            raise ValueError(f"Invalid review status on row {number}: {status}")
        if not (row.get("source") or "").strip():
            raise ValueError(f"Identity rule row {number} needs a source")
        reviewed_at = (row.get("reviewed_at") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reviewed_at):
            raise ValueError(f"Identity rule row {number} needs YYYY-MM-DD reviewed_at")

        missing = [field for field in REQUIRED_FIELDS[action] if not row.get(field)]
        if missing:
            raise ValueError(
                f"Identity rule row {number} needs: {', '.join(missing)}"
            )
    return rows


# load identity pairs that must never merge
def _load_separations(rows):
    separations = []
    for row in rows:
        if _normalize(row.get("action")) != "SEPARATE":
            continue
        name_a = _normalize(row.get("name_a"))
        name_b = _normalize(row.get("name_b"))
        if not name_a or not name_b:
            continue
        place_a = (_normalize(row.get("city_a")), _normalize(row.get("state_a")))
        place_b = (_normalize(row.get("city_b")), _normalize(row.get("state_b")))
        zips = (_normalize(row.get("zip_a"))[:5], _normalize(row.get("zip_b"))[:5])
        separations.append((name_a, name_b, place_a, place_b, zips))

    # ZIPs only for same-city same-name pairs
    zip_names = {
        name
        for name_a, name_b, place_a, place_b, zips in separations
        if all(place_a + place_b + zips) and place_a == place_b
        for name in (name_a, name_b)
    }
    name_pairs = set()
    identity_pairs = set()
    for name_a, name_b, place_a, place_b, zips in separations:
        if not all(place_a + place_b):
            name_pairs.add(frozenset((name_a, name_b)))
            continue
        by_zip = all(zips) and {name_a, name_b} <= zip_names
        identity_pairs.add(frozenset((
            _identity(name_a, *place_a, zips[0] if by_zip else ""),
            _identity(name_b, *place_b, zips[1] if by_zip else ""),
        )))
    return name_pairs, identity_pairs, frozenset(zip_names)


# load donor_key merges, drop to keep
def _load_key_merges(rows) -> dict[str, str]:
    merges = {}
    for row in rows:
        if _normalize(row.get("action")) != "MERGE_KEYS":
            continue
        keep = (row.get("donor_key_a") or "").strip()
        drop = (row.get("donor_key_b") or "").strip()
        if keep and drop and keep != drop:
            merges[drop] = keep
    return merges


# map sub_id to its hold group
def _load_holds(rows) -> dict[str, str]:
    return {
        row["sub_id"].strip(): row["group"].strip()
        for row in rows
        if _normalize(row.get("action")) == "HOLD"
    }


# map name+street+ZIP to its hold group
def _load_held_addresses(rows) -> dict[tuple[str, str, str], str]:
    return {
        (
            _normalize(row.get("name_a")),
            _normalize(row.get("street_a")),
            _normalize(row.get("zip_a"))[:5],
        ): row["group"].strip()
        for row in rows
        if _normalize(row.get("action")) == "HOLD_ADDRESS"
    }


# each row's hold group, '' when none
def held_groups(df, filings=None, addresses=None) -> "pd.Series":
    import pandas as pd

    filings = HELD_FILINGS if filings is None else filings
    addresses = HELD_ADDRESSES if addresses is None else addresses

    groups = pd.Series("", index=df.index, dtype=object)
    if "sub_id" in df.columns and filings:
        groups = df["sub_id"].astype(str).str.strip().map(filings).fillna("")
    columns = ("contributor_name", "contributor_street_1", "contributor_zip")
    if addresses and all(column in df.columns for column in columns):
        place = list(zip(
            df["contributor_name"].map(_normalize),
            df["contributor_street_1"].map(_normalize),
            df["contributor_zip"].map(_normalize).str[:5],
        ))
        by_place = pd.Series([addresses.get(key, "") for key in place], index=df.index)
        groups = groups.where(groups != "", by_place)
    return groups


# group name-merge rules by group label
def _load_name_merges(rows) -> dict[str, tuple[str, ...]]:
    groups = defaultdict(list)
    for row in rows:
        if _normalize(row.get("action")) != "MERGE_NAMES":
            continue
        name = _normalize(row.get("name_a"))
        if not name:
            continue
        group = (row.get("group") or name).strip()
        groups[group].append(name)
    return {group: tuple(names) for group, names in groups.items()}


# last name and first-name words
def _name_words(name: str) -> tuple[str, tuple[str, ...]]:
    last, _, first = _normalize(name).partition(",")
    return last.strip(), tuple(re.findall(r"[A-Z]+", first))


# verified merged names exempt from joint split
def _load_joint_exemptions(rows) -> frozenset[str]:
    exempt = set()
    for row in rows:
        if _normalize(row.get("action")) != "MERGE_KEYS":
            continue
        (last_a, words_a), (last_b, words_b) = (
            _name_words(row.get("name_a")), _name_words(row.get("name_b"))
        )
        if not last_a or last_a != last_b or words_a == words_b:
            continue
        short, long = sorted((words_a, words_b), key=len)
        if short and long[: len(short)] == short:
            exempt.add(f"{last_a}, {' '.join(long)}")
    return frozenset(exempt)


_RULES = _read_rules()
SEPARATE_NAMES, SEPARATE_IDENTITIES, ZIP_SPLIT_NAMES = _load_separations(_RULES)
KEY_MERGES = _load_key_merges(_RULES)
HELD_FILINGS = _load_holds(_RULES)
HELD_ADDRESSES = _load_held_addresses(_RULES)
NAME_MERGES = _load_name_merges(_RULES)
_JOINT_EXEMPT_NAMES = _load_joint_exemptions(_RULES)
_MERGED_NAME_PREFIXES = tuple(
    prefix for prefixes in NAME_MERGES.values() for prefix in prefixes
)


# a verified rule owns this first name
def joint_name_exempt(name: str) -> bool:
    last, words = _name_words(name)
    if not last or not words:
        return False
    text = f"{last}, {' '.join(words)}"
    return text in _JOINT_EXEMPT_NAMES or text.startswith(_MERGED_NAME_PREFIXES)


# true if names have a separation rule
def names_must_stay_separate(name_a: str, name_b: str) -> bool:
    pair = frozenset((_normalize(name_a), _normalize(name_b)))
    return pair in SEPARATE_NAMES


# ZIP5 key part, only for ZIP-split names
def split_zip(name: str, zip_code: str) -> str:
    return _normalize(zip_code)[:5] if _normalize(name) in ZIP_SPLIT_NAMES else ""


# true if rules keep two donors apart
def identities_must_stay_separate(person_a: dict, person_b: dict) -> bool:
    if names_must_stay_separate(person_a["name"], person_b["name"]):
        return True
    pair = frozenset((
        _identity(person_a["name"], person_a["city"], person_a["state"]),
        _identity(person_b["name"], person_b["city"], person_b["state"]),
    ))
    zip_pair = frozenset((
        _identity(person_a["name"], person_a["city"], person_a["state"], person_a.get("zip5", "")[:5]),
        _identity(person_b["name"], person_b["city"], person_b["state"], person_b.get("zip5", "")[:5]),
    ))
    return pair in SEPARATE_IDENTITIES or zip_pair in SEPARATE_IDENTITIES


# follow merge_keys chains to the surviving key
def resolve_donor_key(key: str) -> str:
    seen = set()
    while key in KEY_MERGES and key not in seen:
        seen.add(key)
        key = KEY_MERGES[key]
    return key
