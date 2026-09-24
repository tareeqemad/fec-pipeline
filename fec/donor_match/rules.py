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


def _identity(name: str, city: str, state: str, zip5: str = "") -> str:
    parts = (name, city, state, zip5) if zip5 else (name, city, state)
    return "|".join(map(_normalize, parts))


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
    """(name pairs, name+city+state pairs, names whose rules also give ZIP codes).

    Two people with one name in one city (a physician and a lawyer, both ANDREW
    ROSENBERG in NEW YORK) share one name+city+state profile; a rule in one city
    that also gives zip_a/zip_b splits that name's profiles by ZIP, and every
    rule of that name giving ZIPs then compares profiles by ZIP.
    """
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

    # ZIPs matter only for a name the city cannot tell apart
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


def _name_words(name: str) -> tuple[str, tuple[str, ...]]:
    last, _, first = _normalize(name).partition(",")
    return last.strip(), tuple(re.findall(r"[A-Z]+", first))


def _load_joint_exemptions(rows) -> frozenset[str]:
    """Longer names of verified merge_keys pairs 'LAST, A' + 'LAST, A W...'.

    Such a rule is a reviewed statement that the extra word is the filer's own
    name (GOTTESMAN, MARGERY ARCHIE: Archie is Margery's public name), so the
    joint-filing guard must not split it off.
    """
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
NAME_MERGES = _load_name_merges(_RULES)
_JOINT_EXEMPT_NAMES = _load_joint_exemptions(_RULES)
_MERGED_NAME_PREFIXES = tuple(
    prefix for prefixes in NAME_MERGES.values() for prefix in prefixes
)


def joint_name_exempt(name: str) -> bool:
    """True when a verified rule says this multi-word first name is its filer's own.

    A merge_names group covers every name that starts with one of its names
    (all one person); a merge_keys pair 'LAST, A' / 'LAST, A W' covers 'LAST, A W'.
    """
    last, words = _name_words(name)
    if not last or not words:
        return False
    text = f"{last}, {' '.join(words)}"
    return text in _JOINT_EXEMPT_NAMES or text.startswith(_MERGED_NAME_PREFIXES)


def names_must_stay_separate(name_a: str, name_b: str) -> bool:
    pair = frozenset((_normalize(name_a), _normalize(name_b)))
    return pair in SEPARATE_NAMES


def split_zip(name: str, zip_code: str) -> str:
    """The ZIP5 a profile of this name is also keyed by: only for names a ZIP-level rule splits."""
    return _normalize(zip_code)[:5] if _normalize(name) in ZIP_SPLIT_NAMES else ""


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


def resolve_donor_key(key: str) -> str:
    """Follow verified merge_keys rules, including chains, to the key that survives."""
    seen = set()
    while key in KEY_MERGES and key not in seen:
        seen.add(key)
        key = KEY_MERGES[key]
    return key
