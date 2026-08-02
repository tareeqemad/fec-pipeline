"""Proactive data-quality scanner; run as: python -m fec.cleaning.quality_scan [csv]."""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES, OK_SHORT_OCCUPATIONS
from fec.config.employers import EMPLOYER_ABBREVIATIONS

# every abbreviation token is worth surfacing, auto-expanded or not
_ABBR_RES = {token: re.compile(rf"\b{token}\b") for token in EMPLOYER_ABBREVIATIONS}
_LEGAL = {"LLC", "LLP", "INC", "CORP", "CO", "LTD", "LP", "PLC", "PC", "PA",
          "COMPANY", "CORPORATION", "THE", "AND", "OF"}
# normalize expandable abbreviations so "X MGMT" and "X MANAGEMENT" share a fingerprint
_ABBR_NORM = {token: expansion
              for token, expansion in EMPLOYER_ABBREVIATIONS.items() if expansion}
_STATUS = EMPLOYER_STATUS_VALUES

_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_REPEAT_RE = re.compile(r"(.)\1{2,}")


def _ex(items, n=8):
    return sorted(items)[:n]


def scan_employer_abbreviations(df: pd.DataFrame) -> dict:
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    distinct = {employer for employer in emp.unique() if employer and employer.upper() not in _STATUS}
    per_token = {}
    flagged = set()
    for token, regex in _ABBR_RES.items():
        hits = {employer for employer in distinct if regex.search(employer.upper())}
        if hits:
            per_token[token] = {"count": len(hits), "examples": _ex(hits, 5)}
            flagged |= hits
    return {"distinct_employers_flagged": len(flagged), "by_token": per_token}


def _emp_fp(name: str) -> str:
    # keep single-letter tokens (they split M&R from S&A); drop legal suffixes; normalize abbreviations
    tokens = [_ABBR_NORM.get(token, token) for token in _TOKEN_RE.findall(name.upper())]
    tokens = [token for token in tokens if token not in _LEGAL]
    return " ".join(sorted(tokens))


def scan_employer_near_duplicates(df: pd.DataFrame) -> dict:
    """Distinct employer strings that reduce to the same fingerprint (same firm written differently)."""
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    groups = defaultdict(set)
    for employer in emp.unique():
        if employer and employer.upper() not in _STATUS:
            fingerprint = _emp_fp(employer)
            if fingerprint:
                groups[fingerprint].add(employer)
    dupes = {fingerprint: group for fingerprint, group in groups.items() if len(group) > 1}
    examples = [sorted(group) for group in sorted(dupes.values(), key=lambda group: -len(group))[:8]]
    return {"groups": len(dupes), "examples": examples}


def scan_name_composite_drift(df: pd.DataFrame) -> dict:
    """contributor_name that doesn't match the canonical "LAST, FIRST" rebuilt from first/last."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    first = individuals["contributor_first_name"].fillna("").str.strip()
    last = individuals["contributor_last_name"].fillna("").str.strip()
    rebuilt = (last + ", " + first).where((first != "") & (last != ""), last)
    drift = individuals["contributor_name"].fillna("").str.strip() != rebuilt
    bad = individuals[drift]
    return {"rows": int(drift.sum()), "donors": int(bad["donor_key"].nunique()),
            "examples": _ex({f"{name}  ≠  {rebuilt_name}" for name, rebuilt_name in zip(bad["contributor_name"].head(40), rebuilt[drift].head(40))}, 6)}


def _street_fp(s: str) -> str:
    tokens = _TOKEN_RE.findall(s.upper())
    return (tokens[0] + "|" + " ".join(sorted(tokens[1:]))) if tokens else ""


def scan_address_order_variants(df: pd.DataFrame) -> dict:
    """Per donor: same ZIP + same token-set street written different ways."""
    individuals = df[(df["entity_type"] == "INDIVIDUAL") & (df["contributor_street_1"] != "")]
    n_groups, examples = 0, []
    for (_, _z, _fp), group in individuals.groupby(["donor_key", "contributor_zip",
                                        individuals["contributor_street_1"].map(_street_fp)]):
        streets = group["contributor_street_1"].unique()
        if len(streets) > 1:
            n_groups += 1
            if len(examples) < 8:
                examples.append(sorted(streets))
    return {"groups": n_groups, "examples": examples}


_VOWELS = set("AEIOUY")


def _is_rare_uncategorized(value, counts, occ, cat, uncategorized) -> bool:
    """True only for globally rare values no categorization rule matched; anything else is real."""
    if not value or value in _STATUS or value in OK_SHORT_OCCUPATIONS:
        return False
    if int(counts.get(value, 0)) > 5:
        return False
    return bool((cat[occ == value].isin(uncategorized)).all())


def scan_junk_occupations(df: pd.DataFrame) -> dict:
    """Surface likely keyboard-mash occupations for review; never blanked automatically."""
    if "contributor_occupation" not in df.columns:
        return {"distinct": 0, "rows": 0, "examples": []}
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    occ = individuals["contributor_occupation"].fillna("").astype(str).str.strip().str.upper()
    categories = individuals.get("occupation_category", pd.Series("", index=individuals.index)).fillna("").astype(str).str.upper()

    counts = occ.value_counts()
    uncategorized = {"", "OTHER"}
    suspects = {}
    for value, n_rows in counts.items():
        if not value or value in _STATUS or value in OK_SHORT_OCCUPATIONS:
            continue
        if n_rows > 5:                                   # common: almost surely real
            continue
        if not (categories[occ == value].isin(uncategorized)).all():
            continue                                     # a rule categorized it: real
        # multi-word values are composed titles typed on purpose; mash is a single blob
        if " " in value:
            continue
        letters = [char for char in value if char.isalpha()]
        # vowel-less is the high-precision signal ("short" alone flags real values like BUYER/CLO);
        # word-shaped junk (FDE, UGH) is indistinguishable from real abbreviations and stays human work
        looks_wrong = (
            not (set(letters) & _VOWELS)                 # no vowel at all
            or _REPEAT_RE.search(value)                  # SSSZSS, XXXX
            or len(value) <= 2                           # 2-char, not whitelisted
        )
        if looks_wrong:
            suspects[value] = int(n_rows)

    # donor-history evidence for word-shaped junk (50x DOCTOR then 1x 'ZZZ'); informs the human,
    # never auto-applied -- a lone odd occupation can be a real job change
    evidence = {}
    if "donor_key" in individuals.columns:
        pair = individuals.assign(_o=occ).groupby(["donor_key", "_o"]).size()
        per_donor = individuals.groupby("donor_key").size()
        for (donor_key, value), count in pair.items():
            # only judge values already rare+uncategorized: donor history refines the shortlist,
            # it must never build one (else one donor's 1x ATTORNEY condemns all ATTORNEY rows)
            if not _is_rare_uncategorized(value, counts, occ, categories, uncategorized):
                continue
            total = int(per_donor.get(donor_key, 0))
            # rare-for-this-donor AND the donor is otherwise well-established
            if count <= 2 and total >= 10 and (total - count) >= 8:
                others = sorted({other for other in occ[individuals["donor_key"] == donor_key]
                                 if other and other != value and other not in _STATUS})
                if others:
                    evidence.setdefault(value, []).append(
                        f"{count}x vs {total - count}x {'/'.join(others[:2])}")

    merged = {value: {"rows": count, "why": "shape"} for value, count in suspects.items()}
    for value, evidence_notes in evidence.items():
        merged.setdefault(value, {"rows": int(counts.get(value, 0)), "why": ""})
        merged[value]["why"] = (merged[value]["why"] + "+donor-history").lstrip("+")
        merged[value]["evidence"] = evidence_notes[:2]
    return {
        "distinct": len(merged),
        "rows": int(sum(details["rows"] for details in merged.values())),
        "examples": [{"value": value, **details}
                     for value, details in sorted(merged.items(), key=lambda item: -item[1]["rows"])[:15]],
    }


def scan(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "employer_abbreviations": scan_employer_abbreviations(df),
        "employer_near_duplicates": scan_employer_near_duplicates(df),
        "name_composite_drift": scan_name_composite_drift(df),
        "address_order_variants": scan_address_order_variants(df),
        "junk_occupation_suspects": scan_junk_occupations(df),
    }


def main(argv: list[str]) -> int:
    path = Path(argv[0]) if argv else Path("data/contributions_cleaned.csv")
    if not path.is_file():
        print(f"  file not found: {path}", file=sys.stderr)
        return 1
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)
    report = scan(df)

    print("=" * 60)
    print(f"  Data-quality scan — {report['rows']:,} rows")
    print("=" * 60)
    abbrev_report = report["employer_abbreviations"]
    print(f"\n  Employer abbreviations: {abbrev_report['distinct_employers_flagged']:,} distinct employers")
    for token, details in sorted(abbrev_report["by_token"].items(), key=lambda item: -item[1]["count"]):
        print(f"    {token:8} {details['count']:>4}   e.g. {', '.join(details['examples'][:3])}")
    near_dup_report = report["employer_near_duplicates"]
    print(f"\n  Employer near-duplicates (same firm, diff text): {near_dup_report['groups']:,} groups")
    for group in near_dup_report["examples"][:5]:
        print(f"    - {'  |  '.join(group)}")
    drift_report = report["name_composite_drift"]
    print(f"\n  Name composite drift: {drift_report['rows']:,} rows / {drift_report['donors']:,} donors")
    for example in drift_report["examples"][:4]:
        print(f"    - {example}")
    variant_report = report["address_order_variants"]
    print(f"\n  Address order/spacing variants: {variant_report['groups']:,} per-donor groups")
    for group in variant_report["examples"][:4]:
        print(f"    - {'  |  '.join(group)}")
    junk_report = report["junk_occupation_suspects"]
    print(f"\n  Junk occupation suspects: {junk_report['distinct']:,} values / {junk_report['rows']:,} rows"
          f"{'  — confirm, then add to OCCUPATION_FIXES (junk) or OK_SHORT_OCCUPATIONS (real)' if junk_report['distinct'] else ''}")
    for example in junk_report["examples"][:8]:
        print(f"    - {example['value']}  ({example['rows']} rows)")

    out = path.parent / "quality_scan.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
