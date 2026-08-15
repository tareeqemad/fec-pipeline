"""Read curated contributor name rules."""

import csv
from collections import defaultdict

from fec.env import CONTRIBUTOR_NAME_RULES_CSV


RULE_TYPES = {
    "exact_name",
    "sub_id",
    "first_name",
    "committee_name",
}


def _load_rules() -> dict[str, dict[str, str]]:
    if not CONTRIBUTOR_NAME_RULES_CSV.exists():
        raise FileNotFoundError(
            f"Missing contributor name rules: {CONTRIBUTOR_NAME_RULES_CSV}"
        )

    rules = defaultdict(dict)
    with CONTRIBUTOR_NAME_RULES_CSV.open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            rule_type = (row.get("rule_type") or "").strip()
            raw = (row.get("raw_value") or "").strip()
            corrected = (row.get("corrected_value") or "").strip()
            source = (row.get("source") or "").strip()
            if rule_type not in RULE_TYPES:
                raise ValueError(
                    f"Invalid name rule type on row {number}: {rule_type}"
                )
            if not raw or not corrected or not source:
                raise ValueError(f"Invalid name rule on row {number}")
            if raw in rules[rule_type]:
                raise ValueError(
                    f"Duplicate {rule_type} rule on row {number}: {raw}"
                )
            rules[rule_type][raw] = corrected
    return dict(rules)


_RULES = _load_rules()
EXACT_NAME_CORRECTIONS = _RULES["exact_name"]
ROW_NAME_CORRECTIONS = _RULES["sub_id"]
FIRST_NAME_FIXES = _RULES["first_name"]
COMMITTEE_NAME_FIXES = _RULES["committee_name"]
