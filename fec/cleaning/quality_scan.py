"""Proactive data-quality checks used by clean.py."""

from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES, OK_SHORT_OCCUPATIONS
from fec.config.employers import EMPLOYER_ABBREVIATIONS

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

_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_REPEAT_RE = re.compile(r"(.)\1{2,}")


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
    """Distinct employer strings that reduce to the same fingerprint (same firm written differently)."""
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    groups = defaultdict(set)
    for employer in emp.unique():
        if employer and employer.upper() not in EMPLOYER_STATUS_VALUES:
            fingerprint = _emp_fp(employer)
            if fingerprint:
                groups[fingerprint].add(employer)
    dupes = {
        fingerprint: group for fingerprint, group in groups.items() if len(group) > 1
    }
    examples = [
        sorted(group)
        for group in sorted(dupes.values(), key=lambda group: -len(group))[:8]
    ]
    return {"groups": len(dupes), "examples": examples}


# find contributor_name values that don't match the rebuilt canonical name
def scan_name_composite_drift(df: pd.DataFrame) -> dict:
    """contributor_name that doesn't match the canonical "LAST, FIRST" rebuilt from first/last."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
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
    """Per donor: same ZIP + same token-set street written different ways."""
    individuals = df[
        (df["entity_type"] == "INDIVIDUAL") & (df["contributor_street_1"] != "")
    ]
    n_groups, examples = 0, []
    for (_, _z, _fp), group in individuals.groupby(
        [
            "donor_key",
            "contributor_zip",
            individuals["contributor_street_1"].map(_street_fp),
        ]
    ):
        streets = group["contributor_street_1"].unique()
        if len(streets) > 1:
            n_groups += 1
            if len(examples) < 8:
                examples.append(sorted(streets))
    return {"groups": n_groups, "examples": examples}


_VOWELS = set("AEIOUY")


# true only for globally rare values no categorization rule matched
def _is_rare_uncategorized(value, counts, occ, cat, uncategorized) -> bool:
    """True only for globally rare values no categorization rule matched; anything else is real."""
    if not value or value in EMPLOYER_STATUS_VALUES or value in OK_SHORT_OCCUPATIONS:
        return False
    if int(counts.get(value, 0)) > 5:
        return False
    return bool((cat[occ == value].isin(uncategorized)).all())


# flag rare uncategorized occupation values that look like keyboard mash
def _shape_suspects(occ, categories, counts, uncategorized) -> dict[str, int]:
    suspects = {}
    for value, n_rows in counts.items():
        if (
            not _is_rare_uncategorized(
                value,
                counts,
                occ,
                categories,
                uncategorized,
            )
            or " " in value
        ):
            continue
        letters = [char for char in value if char.isalpha()]
        looks_wrong = (
            not (set(letters) & _VOWELS) or _REPEAT_RE.search(value) or len(value) <= 2
        )
        if looks_wrong:
            suspects[value] = int(n_rows)
    return suspects


# find donor history evidence for suspect occupation values
def _occupation_history_evidence(
    individuals,
    occ,
    categories,
    counts,
    uncategorized,
) -> dict[str, list[str]]:
    if "donor_key" not in individuals.columns:
        return {}

    evidence = {}
    pair = individuals.assign(_o=occ).groupby(["donor_key", "_o"]).size()
    per_donor = individuals.groupby("donor_key").size()
    for (donor_key, value), count in pair.items():
        if not _is_rare_uncategorized(
            value,
            counts,
            occ,
            categories,
            uncategorized,
        ):
            continue
        total = int(per_donor.get(donor_key, 0))
        if count > 2 or total < 10 or (total - count) < 8:
            continue
        others = sorted(
            {
                other
                for other in occ[individuals["donor_key"] == donor_key]
                if other and other != value and other not in EMPLOYER_STATUS_VALUES
            }
        )
        if others:
            evidence.setdefault(value, []).append(
                f"{count}x vs {total - count}x {'/'.join(others[:2])}"
            )
    return evidence


# surface likely keyboard-mash occupations for review
def scan_junk_occupations(df: pd.DataFrame) -> dict:
    """Surface likely keyboard-mash occupations for review."""
    if "contributor_occupation" not in df.columns:
        return {"distinct": 0, "rows": 0, "examples": []}

    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    occ = (
        individuals["contributor_occupation"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    categories = (
        individuals.get(
            "occupation_category",
            pd.Series("", index=individuals.index),
        )
        .fillna("")
        .astype(str)
        .str.upper()
    )
    counts = occ.value_counts()
    uncategorized = {"", "OTHER"}
    suspects = _shape_suspects(occ, categories, counts, uncategorized)
    evidence = _occupation_history_evidence(
        individuals,
        occ,
        categories,
        counts,
        uncategorized,
    )

    merged = {
        value: {"rows": count, "why": "shape"} for value, count in suspects.items()
    }
    for value, evidence_notes in evidence.items():
        merged.setdefault(value, {"rows": int(counts.get(value, 0)), "why": ""})
        merged[value]["why"] = (merged[value]["why"] + "+donor-history").lstrip("+")
        merged[value]["evidence"] = evidence_notes[:2]
    return {
        "distinct": len(merged),
        "rows": int(sum(details["rows"] for details in merged.values())),
        "examples": [
            {"value": value, **details}
            for value, details in sorted(
                merged.items(), key=lambda item: -item[1]["rows"]
            )[:15]
        ],
    }


# list nonblank occupations still uncategorized, by impact
def scan_uncategorized_occupations(df: pd.DataFrame) -> dict:
    """List nonblank occupations still in OTHER, ordered by impact."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    occupation = individuals["contributor_occupation"].fillna("").str.strip()
    category = individuals.get(
        "occupation_category", pd.Series("", index=individuals.index)
    ).fillna("")
    intentional = {"", "EMPLOYED", "NOT DISCLOSED", "OTHER"}
    counts = occupation[
        (category == "OTHER") & ~occupation.isin(intentional)
    ].value_counts()
    return {
        "distinct": int(len(counts)),
        "rows": int(counts.sum()),
        "examples": [
            {"value": value, "rows": int(rows)}
            for value, rows in counts.head(25).items()
        ],
    }


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
