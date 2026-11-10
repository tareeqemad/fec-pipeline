"""Exact, externally verified address corrections (address_rules.csv).

A rule matches a filed street, state and ZIP, optionally one unit or one
filing, and replaces the cells it fills; [CLEAR] empties the street or unit.
"""
import pandas as pd

from fec.env import ADDRESS_RULES_CSV
from fec.io import write_csv_atomic
from fec.log import get_logger

logger = get_logger(__name__)

CLEAR = "[CLEAR]"
MATCH = ("match_street", "match_state", "match_zip", "match_unit", "match_sub_id")
FIXED = {
    "street": "contributor_street_1", "unit": "contributor_street_2", "city": "contributor_city",
    "state": "contributor_state", "zip": "contributor_zip",
}
# What a city correction saved by geocoding fills, and nothing else.
CITY_RULE = ("match_street", "match_state", "match_zip", "match_sub_id", "street", "city", "source")


def _read_rules() -> pd.DataFrame:
    """The rules as text; a blank match_zip matches filings with no ZIP (a foreign address)."""
    rules = pd.read_csv(ADDRESS_RULES_CSV, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    rules = rules.apply(lambda column: column.str.strip())
    rules[list(MATCH[:4])] = rules[list(MATCH[:4])].apply(lambda column: column.str.upper())
    invalid = (rules[["match_street", "match_state", "street", "source"]].eq("").any(axis=1)
               | (rules["unit"].eq(CLEAR) & rules["match_unit"].eq("")) | rules.duplicated(list(MATCH)))
    if invalid.any():
        raise ValueError(f"Invalid or duplicate address rule on row(s) {[row + 2 for row in rules.index[invalid]]}")
    return rules


def apply_verified_address_fixes(df: pd.DataFrame, *, city_only: bool = False, warn_unused: bool = False) -> int:
    """Apply the rules in file order and return the filings changed.

    A rule that only sets the city also runs after the alignment steps
    (city_only), which may have changed the city again.
    """
    rules = _read_rules()
    if city_only:
        rules = rules[rules["street"].str.upper().eq(rules["match_street"]) & rules["city"].ne("")
                      & rules[["unit", "state", "zip"]].eq("").all(axis=1)]
    columns = list(FIXED.values())
    df[columns[1]] = df[columns[1]].astype(object)
    before = df[columns].fillna("")
    sub_ids = df["sub_id"].fillna("").astype(str).str.strip()
    unused = []
    for rule in rules.itertuples(index=False):
        mask = (df[columns[0]].fillna("").eq(rule.match_street) & df[columns[3]].fillna("").eq(rule.match_state)
                & df[columns[4]].fillna("").eq(rule.match_zip))
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


def save_verified_city_corrections(rows: list[dict]) -> int:
    """Add city rules for single filings that have no rule yet; return how many were added."""
    if not rows:
        return 0
    rules = pd.read_csv(ADDRESS_RULES_CSV, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    new = pd.DataFrame(rows).fillna("").astype(str).apply(lambda column: column.str.strip())
    upper = [field for field in CITY_RULE if field not in ("match_sub_id", "source")]
    new[upper] = new[upper].apply(lambda column: column.str.upper())
    if (set(new.columns) != set(CITY_RULE) or new.eq("").any(axis=None) or new["street"].ne(new["match_street"]).any()
            or not new["source"].str.startswith(("https://", "http://")).all()):
        raise ValueError("A verified city correction needs an exact filing and unchanged street")
    key = ["match_street", "match_state", "match_zip", "match_sub_id"]
    taken = set(map(tuple, rules[key].apply(lambda column: column.str.strip().str.upper()).to_numpy()))
    new = new.drop_duplicates(key)
    new = new[[tuple(row) not in taken and (*row[:3], "") not in taken for row in new[key].to_numpy()]]
    if len(new):
        write_csv_atomic(pd.concat([rules, new.reindex(columns=rules.columns, fill_value="")]), ADDRESS_RULES_CSV, index=False)
    return len(new)
