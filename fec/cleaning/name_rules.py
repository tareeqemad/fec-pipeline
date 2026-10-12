"""Read curated contributor name rules."""

import csv
import re
from collections import defaultdict

from fec.env import CONTRIBUTOR_NAME_RULES_CSV


RULE_TYPES = {
    "exact_name",
    "sub_id",
    "first_name",
    "committee_name",
}

_ZIP5_RE = re.compile(r"\d{5}")


# parse first_name rule; surname and ZIP required
def _first_name_rule(
    number: int, raw: str, corrected: str, zip_field: str
) -> tuple[list[tuple[str, str, str]], str]:
    last, comma, first = raw.partition(",")
    last, first = last.strip(), first.strip()
    if not comma or "," in first or not last or not first or len(first.split()) != 1:
        raise ValueError(
            f"first_name rule on row {number} must read 'SURNAME, GARBLED': {raw}"
        )
    if "," in corrected or len(corrected.split()) != 1:
        raise ValueError(
            f"first_name rule on row {number} must correct one word: {corrected}"
        )
    zips = [part.strip() for part in zip_field.split("|")] if zip_field else []
    if not zips or not all(_ZIP5_RE.fullmatch(z) for z in zips):
        raise ValueError(
            f"first_name rule on row {number} needs the filer's ZIP5 "
            f"('|'-separated): {zip_field!r}"
        )
    return [(last, first, z) for z in zips], corrected


# load and validate all name rules
def _load_rules(path=CONTRIBUTOR_NAME_RULES_CSV) -> dict[str, dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing contributor name rules: {path}")

    rules = defaultdict(dict)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            rule_type = (row.get("rule_type") or "").strip()
            raw = (row.get("raw_value") or "").strip()
            corrected = (row.get("corrected_value") or "").strip()
            source = (row.get("source") or "").strip()
            zip_field = (row.get("zip5") or "").strip()
            if rule_type not in RULE_TYPES:
                raise ValueError(
                    f"Invalid name rule type on row {number}: {rule_type}"
                )
            if not raw or not corrected or not source:
                raise ValueError(f"Invalid name rule on row {number}")
            if rule_type == "first_name":
                keys, corrected = _first_name_rule(number, raw, corrected, zip_field)
            elif zip_field:
                raise ValueError(
                    f"zip5 only scopes first_name rules (row {number}, {rule_type})"
                )
            else:
                keys = [raw]
            for key in keys:
                if key in rules[rule_type]:
                    raise ValueError(
                        f"Duplicate {rule_type} rule on row {number}: {raw}"
                    )
                rules[rule_type][key] = corrected
    return {rule_type: dict(rules.get(rule_type, {})) for rule_type in RULE_TYPES}


_RULES = _load_rules()
EXACT_NAME_CORRECTIONS = _RULES["exact_name"]
ROW_NAME_CORRECTIONS = _RULES["sub_id"]
# (surname, garbled word, zip5) to corrected word
FIRST_NAME_FIXES = _RULES["first_name"]
COMMITTEE_NAME_FIXES = _RULES["committee_name"]
