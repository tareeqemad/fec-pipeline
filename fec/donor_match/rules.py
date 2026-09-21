"""Read curated donor identity rules from one CSV."""

import csv
import re
from collections import defaultdict

from fec.env import DATA_DIR


RULES_PATH = DATA_DIR / "database" / "donor_identity_rules.csv"
VALID_ACTIONS = {"merge_keys", "merge_names", "separate"}
VALID_REVIEW_STATUSES = {"verified_fec", "verified_web"}
REQUIRED_FIELDS = {
    "merge_keys": ("donor_key_a", "donor_key_b"),
    "merge_names": ("name_a",),
    "separate": ("name_a", "name_b"),
}


def _normalize(value: str) -> str:
    return " ".join(str(value or "").upper().split())


def _identity(name: str, city: str, state: str) -> str:
    return "|".join(map(_normalize, (name, city, state)))


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


def _load_separations(rows):
    name_pairs = set()
    identity_pairs = set()

    for row in rows:
        if _normalize(row.get("action")) != "SEPARATE":
            continue
        name_a = _normalize(row.get("name_a"))
        name_b = _normalize(row.get("name_b"))
        if not name_a or not name_b:
            continue

        locations = (
            row.get("city_a"), row.get("state_a"),
            row.get("city_b"), row.get("state_b"),
        )
        if all(_normalize(value) for value in locations):
            identity_pairs.add(frozenset((
                _identity(name_a, row["city_a"], row["state_a"]),
                _identity(name_b, row["city_b"], row["state_b"]),
            )))
        else:
            name_pairs.add(frozenset((name_a, name_b)))

    return name_pairs, identity_pairs


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


_RULES = _read_rules()
SEPARATE_NAMES, SEPARATE_IDENTITIES = _load_separations(_RULES)
KEY_MERGES = _load_key_merges(_RULES)
NAME_MERGES = _load_name_merges(_RULES)


def names_must_stay_separate(name_a: str, name_b: str) -> bool:
    pair = frozenset((_normalize(name_a), _normalize(name_b)))
    return pair in SEPARATE_NAMES


def identities_must_stay_separate(person_a: dict, person_b: dict) -> bool:
    if names_must_stay_separate(person_a["name"], person_b["name"]):
        return True
    pair = frozenset((
        _identity(person_a["name"], person_a["city"], person_a["state"]),
        _identity(person_b["name"], person_b["city"], person_b["state"]),
    ))
    return pair in SEPARATE_IDENTITIES


def resolve_donor_key(key: str) -> str:
    """Follow verified merge_keys rules, including chains, to the key that survives."""
    seen = set()
    while key in KEY_MERGES and key not in seen:
        seen.add(key)
        key = KEY_MERGES[key]
    return key
