"""Detect-only scans for junk and uncategorized occupations."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES, OK_SHORT_OCCUPATIONS

_VOWELS = set("AEIOUY")

# the same character repeated 3+ times, e.g. "AAAA", "XXXXX" (junk value)
_REPEAT_RE = re.compile(r"(.)\1{2,}")


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
