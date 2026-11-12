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
    variant, canonical = ((row.get(column) or "").strip().upper() for column in ("variant", "canonical"))
    source, anchor, cycle = ((row.get(column) or "").strip() for column in ("source", "anchor_sub_id", "cycle"))
    action = (row.get("action") or "rename").strip()
    if not variant or not canonical or not source or action != "rename":
        raise ValueError(f"Invalid employer rule on row {number}")
    if cycle and (not anchor or not cycle.isdigit() or int(cycle) % 2):
        raise ValueError(f"Invalid employer rule cycle on row {number}")
    return variant, canonical, anchor, cycle


def read_company_name_rules() -> tuple[dict[str, str], list[tuple[str, str, str, str]]]:
    """Read the reviewed spellings: variant -> canonical, everywhere or for one donor (anchor_sub_id) in one cycle."""
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
    aliases = {}
    for variant, canonical in synonyms.items():
        for name in (variant, canonical):
            key = employer_rule_key(name)
            if key in aliases and aliases[key] != canonical:
                raise ValueError(f"Conflicting company spellings in employer rules: {name}")
            aliases[key] = canonical
    return aliases


_SYNONYMS, _SCOPED_RULES = read_company_name_rules()
# Resolve explicit chains; a spelling cannot acquire two meanings globally.
_resolve_chains(_SYNONYMS)
_ALIASES = _build_aliases(_SYNONYMS)


def documented_employer_name(name: object) -> str | None:
    """Return the reviewed name for a company spelling, or None if the rules do not list it."""
    return _ALIASES.get(employer_rule_key(name))


def _scoped_employer_rules(df: pd.DataFrame) -> dict:
    # Resolve evidence to its current donor; never match by company.
    confirmed = df["entity_type"].eq("INDIVIDUAL") & df["identity_status"].eq("confirmed")
    anchors = dict(zip(df.loc[confirmed, "sub_id"].astype(str), df.loc[confirmed, "donor_key"]))
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
    spellings = (key, employer_rule_key(_ALIASES.get(key, key)))
    for period in (str(cycle), ""):
        for spelling in spellings:
            target = scoped.get((str(donor_key), period, spelling))
            if target:
                return target
    return None


def reviewed_employer_name(name: object, donor_key: str = "", cycle: str = "", scoped: dict | None = None) -> str:
    """Return the reviewed spelling of an employer name; a scoped rule for this donor and cycle wins."""
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


def canonicalize_current_employers(df: pd.DataFrame) -> int:
    """Apply the reviewed names to the current employer; never fill a missing one."""
    scoped = _scoped_employer_rules(df)
    values = df.loc[df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].notna(), "contributor_employer"]
    rows = values.index
    donors = df.loc[rows, "donor_key"].fillna("").astype(str)
    cycles = df.loc[rows, "two_year_transaction_period"].fillna("").astype(str)
    confirmed = df.loc[rows, "identity_status"].eq("confirmed")
    filings = zip(values, donors, cycles, confirmed)
    names = [
        reviewed_employer_name(text, donor, cycle, scoped if known else None) for text, donor, cycle, known in filings
    ]
    names = pd.Series(names, index=rows, dtype=object)
    different = names.ne(values)
    df.loc[rows[different], "contributor_employer"] = names[different]
    return int(different.sum())
