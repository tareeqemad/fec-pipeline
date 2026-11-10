"""Exact, externally verified address corrections (address_rules.csv).

A rule matches a filed street, state and ZIP, optionally one unit or one
filing, and replaces the cells it fills; [CLEAR] empties the street or unit.
"""

import pandas as pd

from fec.common.log import get_logger
from fec.common.settings import ADDRESS_RULES_CSV

logger = get_logger(__name__)

CLEAR = "[CLEAR]"

MATCH = ("match_street", "match_state", "match_zip", "match_unit", "match_sub_id")

FIXED = {
    "street": "contributor_street_1",
    "unit": "contributor_street_2",
    "city": "contributor_city",
    "state": "contributor_state",
    "zip": "contributor_zip",
}


def _read_address_rules() -> pd.DataFrame:
    """Read the rules as text; a blank match_zip matches filings with no ZIP (a foreign address)."""
    rules = pd.read_csv(ADDRESS_RULES_CSV, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    rules = rules.apply(lambda column: column.str.strip())
    rules[list(MATCH[:4])] = rules[list(MATCH[:4])].apply(lambda column: column.str.upper())
    invalid = (
        rules[["match_street", "match_state", "street", "source"]].eq("").any(axis=1)
        | (rules["unit"].eq(CLEAR) & rules["match_unit"].eq(""))
        | rules.duplicated(list(MATCH))
    )
    if invalid.any():
        raise ValueError(f"Invalid or duplicate address rule on row(s) {[row + 2 for row in rules.index[invalid]]}")
    return rules


def apply_address_rules(df: pd.DataFrame, *, city_only: bool = False, warn_unused: bool = False) -> int:
    """Apply the rules in file order and return the filings changed.

    A rule that only sets the city also runs after the alignment steps
    (city_only), which may have changed the city again.
    """
    rules = _read_address_rules()
    if city_only:
        rules = rules[
            rules["street"].str.upper().eq(rules["match_street"])
            & rules["city"].ne("")
            & rules[["unit", "state", "zip"]].eq("").all(axis=1)
        ]
    columns = list(FIXED.values())
    df[columns[1]] = df[columns[1]].astype(object)
    before = df[columns].fillna("")
    sub_ids = df["sub_id"].fillna("").astype(str).str.strip()
    unused = []
    for rule in rules.itertuples(index=False):
        mask = (
            df[columns[0]].fillna("").eq(rule.match_street)
            & df[columns[3]].fillna("").eq(rule.match_state)
            & df[columns[4]].fillna("").eq(rule.match_zip)
        )
        if rule.match_unit:
            mask &= df[columns[1]].fillna("").eq(rule.match_unit)
        if rule.match_sub_id:
            mask &= sub_ids.eq(rule.match_sub_id)
        if not mask.any():
            unused.append(f"{rule.match_street} | {rule.match_state} | {rule.match_zip}")
        for field, column in FIXED.items():
            value = getattr(rule, field)
            if value:
                df.loc[mask, column] = pd.NA if value == CLEAR else value
    # A rule that matches nothing is mistyped or no longer needed.
    if warn_unused and unused:
        logger.warning("WARNING: %s address rules matched no filing: %s", len(unused), "; ".join(unused[:5]))
    return int(df[columns].fillna("").ne(before).any(axis=1).sum())
