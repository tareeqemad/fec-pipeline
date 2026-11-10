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


def _load_rules() -> tuple[dict[str, str], list[tuple[str, str, str, str]]]:
    """Reviewed spellings: variant -> canonical everywhere, or for one donor (anchor_sub_id) in one cycle."""
    aliases, scoped = {}, []
    with EMPLOYER_NAME_RULES_CSV.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            variant = (row.get("variant") or "").strip().upper()
            canonical = (row.get("canonical") or "").strip().upper()
            source = (row.get("source") or "").strip()
            anchor = (row.get("anchor_sub_id") or "").strip()
            cycle = (row.get("cycle") or "").strip()
            if not variant or not canonical or not source or (row.get("action") or "rename").strip() != "rename":
                raise ValueError(f"Invalid employer rule on row {number}")
            if cycle and (not anchor or not cycle.isdigit() or int(cycle) % 2):
                raise ValueError(f"Invalid employer rule cycle on row {number}")
            if anchor:
                scoped.append((anchor, cycle, employer_rule_key(variant), canonical))
            else:
                if variant in aliases and aliases[variant] != canonical:
                    raise ValueError(f"Conflicting employer rule on row {number}")
                aliases[variant] = canonical
    return aliases, scoped


EMPLOYER_SYNONYMS, _SCOPED_RULES = _load_rules()

# Resolve explicit chains; a spelling cannot acquire two meanings globally.
for _variant in EMPLOYER_SYNONYMS:
    _seen = {_variant}
    _target = EMPLOYER_SYNONYMS[_variant]
    while _target in EMPLOYER_SYNONYMS and EMPLOYER_SYNONYMS[_target] != _target:
        if _target in _seen:
            raise ValueError(f"Cycle in employer rules: {_variant}")
        _seen.add(_target)
        _target = EMPLOYER_SYNONYMS[_target]
    EMPLOYER_SYNONYMS[_variant] = _target

_ALIASES = {}

for _variant, _canonical in EMPLOYER_SYNONYMS.items():
    for _name in (_variant, _canonical):
        _key = employer_rule_key(_name)
        if _key in _ALIASES and _ALIASES[_key] != _canonical:
            raise ValueError(f"Conflicting company spellings in employer rules: {_name}")
        _ALIASES[_key] = _canonical


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


def reviewed_employer_name(
    name: object, donor_key: str = "", cycle: str = "", scoped: dict[tuple[str, str, str], str] | None = None
) -> str:
    """Return the reviewed spelling of an employer name, for this donor and cycle when a scoped rule applies."""
    if not isinstance(name, str):
        return ""
    key = employer_rule_key(name)
    if scoped:
        spellings = (key, employer_rule_key(_ALIASES.get(key, key)))
        for period in (str(cycle), ""):
            for spelling in spellings:
                if target := scoped.get((str(donor_key), period, spelling)):
                    return target
    if key in _ALIASES:
        return _ALIASES[key]
    # Keep dots, name words and legal forms; tidy punctuation only.
    text = name.upper().replace("&", " AND ").replace(",", " ")
    text = re.sub(r"\.(?!(?:COM|ORG|NET|IO|AI|US|EDU|GOV)\b)", "", text)
    return " ".join(text.split())


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
            names = pd.Series(
                [
                    reviewed_employer_name(value, donor, cycle, scoped if known else None)
                    for value, donor, cycle, known in zip(
                        values,
                        donors.loc[values.index],
                        cycles.loc[values.index],
                        confirmed.loc[values.index],
                    )
                ],
                index=values.index,
                dtype=object,
            )
        else:
            names = values.map(reviewed_employer_name)
        different = names.ne(values)
        df.loc[values.index[different], column] = names[different]
        changed += int(different.sum())
    return changed


def canonicalize_current_employers(df: pd.DataFrame) -> int:
    """Apply documented names to the current employer only."""
    return apply_reviewed_employer_names(df, ("contributor_employer",))
