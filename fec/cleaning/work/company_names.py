"""Company names supported by the shared rules file."""

import csv
import re

import pandas as pd

from fec.common.settings import EMPLOYER_NAME_RULES_CSV


def employer_rule_key(name: object) -> str:
    """Normalise formatting only: keep words, their order, and legal forms."""
    if not isinstance(name, str):
        return ""
    name = name.upper().replace("&", " AND ")
    return " ".join(name.replace(",", " ").replace(".", "").split())


def _read_company_rule(number: int, row: dict[str, str]) -> tuple[str, str, str, str]:
    """Check one row of the employer rules; return its variant, canonical, anchor and cycle."""
    variant = (row.get("variant") or "").strip().upper()
    canonical = (row.get("canonical") or "").strip().upper()
    source = (row.get("source") or "").strip()
    anchor = (row.get("anchor_sub_id") or "").strip()
    cycle = (row.get("cycle") or "").strip()
    action = (row.get("action") or "rename").strip()
    if not variant or not canonical or not source or action != "rename":
        raise ValueError(f"Invalid employer rule on row {number}")
    if cycle and (not anchor or not cycle.isdigit() or int(cycle) % 2):
        raise ValueError(f"Invalid employer rule cycle on row {number}")
    return variant, canonical, anchor, cycle


def read_company_name_rules() -> tuple[dict[str, str], list[tuple[str, str, str, str]]]:
    """Read the reviewed spellings: variant -> canonical.

    A rule holds everywhere, or for one donor (anchor_sub_id) in one cycle.
    """
    aliases, scoped = {}, []
    with EMPLOYER_NAME_RULES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            variant, canonical, anchor, cycle = _read_company_rule(number, row)
            if anchor:
                scoped.append((anchor, cycle, employer_rule_key(variant), canonical))
                continue
            if variant in aliases and aliases[variant] != canonical:
                raise ValueError(f"Conflicting employer rule on row {number}")
            aliases[variant] = canonical
    return aliases, scoped


def _resolve_chains(synonyms: dict[str, str]) -> None:
    """Point each variant at the end of its chain of renames; a cycle is an error."""
    for variant in synonyms:
        seen = {variant}
        target = synonyms[variant]
        while target in synonyms and synonyms[target] != target:
            if target in seen:
                raise ValueError(f"Cycle in employer rules: {variant}")
            seen.add(target)
            target = synonyms[target]
        synonyms[variant] = target


def _build_aliases(synonyms: dict[str, str]) -> dict[str, str]:
    """Map the key of every variant and canonical spelling to the canonical name."""
    aliases = {}
    for variant, canonical in synonyms.items():
        for name in (variant, canonical):
            key = employer_rule_key(name)
            if key in aliases and aliases[key] != canonical:
                raise ValueError(f"Conflicting company spellings in employer rules: {name}")
            aliases[key] = canonical
    return aliases


EMPLOYER_SYNONYMS, _SCOPED_RULES = read_company_name_rules()

# Resolve explicit chains; a spelling cannot acquire two meanings globally.
_resolve_chains(EMPLOYER_SYNONYMS)

_ALIASES = _build_aliases(EMPLOYER_SYNONYMS)


def documented_employer_name(name: object) -> str | None:
    """Return the reviewed name for a company spelling, or None if the rules do not list it."""
    return _ALIASES.get(employer_rule_key(name))


def scoped_employer_rules(df: pd.DataFrame) -> dict:
    """Resolve evidence to its current donor; never match by company."""
    needed = {"sub_id", "donor_key", "entity_type", "identity_status"}
    if not _SCOPED_RULES or not needed.issubset(df.columns):
        return {}
    confirmed = df["entity_type"].eq("INDIVIDUAL") & df["identity_status"].eq("confirmed")
    anchors = df.loc[confirmed, ["sub_id", "donor_key"]].copy()
    anchors["sub_id"] = anchors["sub_id"].astype(str)
    anchors = anchors.set_index("sub_id")["donor_key"].to_dict()
    rules = {}
    for anchor, cycle, variant, canonical in _SCOPED_RULES:
        donor = anchors.get(anchor)
        if not donor:
            continue
        canonical = reviewed_employer_name(canonical)
        key = (str(donor), cycle, variant)
        if key in rules and rules[key] != canonical:
            raise ValueError(f"Conflicting employer rules for evidence filing {anchor}")
        rules[key] = canonical
    return rules


def _find_scoped_name(key: str, donor_key: str, cycle: str, scoped: dict[tuple[str, str, str], str]) -> str | None:
    """Return the name a scoped rule gives this donor, this cycle first, or None."""
    spellings = (key, employer_rule_key(_ALIASES.get(key, key)))
    for period in (str(cycle), ""):
        for spelling in spellings:
            target = scoped.get((str(donor_key), period, spelling))
            if target:
                return target
    return None


def reviewed_employer_name(
    name: object,
    donor_key: str = "",
    cycle: str = "",
    scoped: dict[tuple[str, str, str], str] | None = None,
) -> str:
    """Return the reviewed spelling of an employer name.

    A scoped rule for this donor and cycle wins.
    """
    if not isinstance(name, str):
        return ""
    key = employer_rule_key(name)
    if scoped:
        target = _find_scoped_name(key, donor_key, cycle, scoped)
        if target:
            return target
    if key in _ALIASES:
        return _ALIASES[key]
    # Keep dots, name words and legal forms; tidy punctuation only.
    text = name.upper().replace("&", " AND ").replace(",", " ")
    text = re.sub(r"\.(?!(?:COM|ORG|NET|IO|AI|US|EDU|GOV)\b)", "", text)
    return " ".join(text.split())


def _review_names_per_donor(
    values: pd.Series,
    donors: pd.Series,
    cycles: pd.Series,
    confirmed: pd.Series,
    scoped: dict[tuple[str, str, str], str],
) -> pd.Series:
    """Return the reviewed name of each value; scoped rules apply to confirmed donors only."""
    rows = values.index
    return pd.Series(
        [
            reviewed_employer_name(value, donor, cycle, scoped if known else None)
            for value, donor, cycle, known in zip(values, donors.loc[rows], cycles.loc[rows], confirmed.loc[rows])
        ],
        index=rows,
        dtype=object,
    )


def apply_reviewed_employer_names(
    df: pd.DataFrame,
    columns: tuple[str, ...] = ("contributor_employer", "previous_employer"),
) -> int:
    """Aliases change existing values only, never fill missing work."""
    scoped = scoped_employer_rules(df)
    mask = df["entity_type"].eq("INDIVIDUAL") if "entity_type" in df else pd.Series(True, index=df.index)
    donors = df.get("donor_key", pd.Series("", index=df.index)).fillna("").astype(str)
    cycles = df.get("two_year_transaction_period", pd.Series("", index=df.index)).fillna("").astype(str)
    confirmed = df.get("identity_status", pd.Series("", index=df.index)).eq("confirmed")
    changed = 0
    for column in columns:
        if column not in df:
            continue
        values = df.loc[mask & df[column].notna(), column]
        if scoped:
            names = _review_names_per_donor(values, donors, cycles, confirmed, scoped)
        else:
            names = values.map(reviewed_employer_name)
        different = names.ne(values)
        df.loc[values.index[different], column] = names[different]
        changed += int(different.sum())
    return changed


def canonicalize_current_employers(df: pd.DataFrame) -> int:
    """Apply documented names to the current employer only."""
    return apply_reviewed_employer_names(df, ("contributor_employer",))
