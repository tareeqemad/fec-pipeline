"""Report data-quality issues during cleaning."""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from itertools import combinations

import pandas as pd

from fec.cleaning.reports.occupation_scan import (
    scan_junk_occupations,
    scan_uncategorized_occupations,
)
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.config.employers import EMPLOYER_ABBREVIATIONS
from fec.cleaning.work.employer_aliases import reviewed_employer_pair

# every abbreviation token is worth surfacing, auto-expanded or not
_ABBR_RES = {token: re.compile(rf"\b{token}\b") for token in EMPLOYER_ABBREVIATIONS}
_LEGAL = {
    "LLC",
    "LLP",
    "INC",
    "CORP",
    "CO",
    "LTD",
    "LP",
    "PLC",
    "PC",
    "PA",
    "COMPANY",
    "CORPORATION",
    "THE",
    "AND",
    "OF",
}
# normalize expandable abbreviations so "X MGMT" and "X MANAGEMENT" share a fingerprint
_ABBR_NORM = {
    token: expansion for token, expansion in EMPLOYER_ABBREVIATIONS.items() if expansion
}

# a run of letters/digits, used to tokenize a name into words
_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# sorted first n items, for report examples
def _ex(items, n=8):
    return sorted(items)[:n]


# surface employers containing known abbreviation tokens
def scan_employer_abbreviations(df: pd.DataFrame) -> dict:
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    distinct = {
        employer
        for employer in emp.unique()
        if employer and employer.upper() not in EMPLOYER_STATUS_VALUES
    }
    per_token = {}
    flagged = set()
    for token, regex in _ABBR_RES.items():
        hits = {employer for employer in distinct if regex.search(employer.upper())}
        if hits:
            per_token[token] = {"count": len(hits), "examples": _ex(hits, 5)}
            flagged |= hits
    return {"distinct_employers_flagged": len(flagged), "by_token": per_token}


# build a comparable fingerprint for an employer name
def _emp_fp(name: str) -> str:
    # keep single-letter tokens (they split M&R from S&A); drop legal suffixes; normalize abbreviations
    tokens = [_ABBR_NORM.get(token, token) for token in _TOKEN_RE.findall(name.upper())]
    tokens = [token for token in tokens if token not in _LEGAL]
    return " ".join(sorted(tokens))


# group employer strings that reduce to the same fingerprint
def scan_employer_near_duplicates(df: pd.DataFrame) -> dict:
    columns = [name for name in ("contributor_employer", "previous_employer") if name in df]
    people = df.loc[df["entity_type"] == "INDIVIDUAL"]
    emp = pd.concat([people[column] for column in columns], ignore_index=True).fillna("")
    groups = defaultdict(set)
    for employer in emp.unique():
        if employer and employer.upper() not in EMPLOYER_STATUS_VALUES:
            fingerprint = _emp_fp(employer)
            if fingerprint:
                groups[fingerprint].add(employer)
    candidates = {tuple(sorted(pair)) for group in groups.values()
                  for pair in combinations(group, 2)}
    # Same-donor similarity is a suggestion, never a rename.
    if "donor_key" in people:
        for _, donor in people.groupby("donor_key"):
            names = {name for column in columns for name in donor[column].dropna().unique()
                     if name and name.upper() not in EMPLOYER_STATUS_VALUES}
            for left, right in combinations(sorted(names), 2):
                if SequenceMatcher(None, left, right).ratio() >= 0.9:
                    candidates.add((left, right))
    dupes = [pair for pair in sorted(candidates) if not reviewed_employer_pair(*pair)]
    examples = [
        sorted(group)
        for group in sorted(dupes, key=lambda group: -len(group))[:8]
    ]
    return {"groups": len(dupes), "examples": examples,
            "kept_separate_by_rule": len(candidates) - len(dupes)}


# find contributor_name values that don't match the rebuilt canonical name
def scan_name_composite_drift(df: pd.DataFrame) -> dict:
    individuals = df.loc[df["entity_type"] == "INDIVIDUAL", [
        "contributor_first_name", "contributor_last_name", "contributor_name", "donor_key",
    ]]
    first = individuals["contributor_first_name"].fillna("").str.strip()
    last = individuals["contributor_last_name"].fillna("").str.strip()
    rebuilt = (last + ", " + first).where((first != "") & (last != ""), last)
    drift = individuals["contributor_name"].fillna("").str.strip() != rebuilt
    bad = individuals[drift]
    return {
        "rows": int(drift.sum()),
        "donors": int(bad["donor_key"].nunique()),
        "examples": _ex(
            {
                f"{name}  ≠  {rebuilt_name}"
                for name, rebuilt_name in zip(
                    bad["contributor_name"].head(40), rebuilt[drift].head(40)
                )
            },
            6,
        ),
    }


# build a token-set fingerprint for a street string
def _street_fp(s: str) -> str:
    tokens = _TOKEN_RE.findall(s.upper())
    return (tokens[0] + "|" + " ".join(sorted(tokens[1:]))) if tokens else ""


# find same-donor streets written in different word order
def scan_address_order_variants(df: pd.DataFrame) -> dict:
    individuals = df.loc[
        (df["entity_type"] == "INDIVIDUAL") & (df["contributor_street_1"] != ""),
        ["donor_key", "contributor_zip", "contributor_street_1"],
    ]
    n_groups, examples = 0, []
    for _, streets in individuals.groupby(
        [
            "donor_key",
            "contributor_zip",
            individuals["contributor_street_1"].map(_street_fp),
        ]
    )["contributor_street_1"]:
        streets = streets.unique()
        if len(streets) > 1:
            n_groups += 1
            if len(examples) < 8:
                examples.append(sorted(streets))
    return {"groups": n_groups, "examples": examples}


# run all data-quality scans and collect their results
def scan(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "employer_abbreviations": scan_employer_abbreviations(df),
        "employer_near_duplicates": scan_employer_near_duplicates(df),
        "name_composite_drift": scan_name_composite_drift(df),
        "address_order_variants": scan_address_order_variants(df),
        "junk_occupation_suspects": scan_junk_occupations(df),
        "uncategorized_occupations": scan_uncategorized_occupations(df),
    }
