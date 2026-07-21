"""quality_scan.py — proactive data-quality scanner.

Enumerates *classes* of suspicious cases in the cleaned output so problems are
found systematically (not one-by-one by eye). Each scanner returns a count + a
handful of examples; the CLI prints a summary and writes data/quality_scan.json.

    python -m fec.cleaning.quality_scan [data/contributions_cleaned.csv]

Findings feed two places: the safe classes are auto-fixed by the cleaning
passes, the judgement calls go to a manual review report.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

# Employer abbreviations worth surfacing (whether or not auto-expanded yet).
_ABBR_TOKENS = ["MGMT", "MGT", "MGMNT", "INV", "INTL", "INTNL", "ASSOC", "ASSOCS",
                "SVC", "SVCS", "GRP", "MFG", "MKTG", "CONSTR", "DEVELOP", "TECHS"]
_LEGAL = {"LLC", "LLP", "INC", "CORP", "CO", "LTD", "LP", "PLC", "PC", "PA",
          "COMPANY", "CORPORATION", "THE", "AND", "OF"}
# normalize a few abbreviations so "X MGMT" and "X MANAGEMENT" share a fingerprint
_ABBR_NORM = {"MGMT": "MANAGEMENT", "MGT": "MANAGEMENT", "MGMNT": "MANAGEMENT",
              "INTL": "INTERNATIONAL", "GRP": "GROUP", "SVC": "SERVICES",
              "SVCS": "SERVICES", "MFG": "MANUFACTURING", "INV": "INVESTMENT"}
_STATUS = {"RETIRED", "SELF-EMPLOYED", "SELF EMPLOYED", "NOT EMPLOYED", "UNEMPLOYED",
           "NONE", "N/A", "NA", "STUDENT", "HOMEMAKER", "CAMPAIGN/COMMITTEE", "NOT DISCLOSED", ""}


def _ex(items, n=8):
    return sorted(items)[:n]


def scan_employer_abbreviations(df: pd.DataFrame) -> dict:
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    distinct = {e for e in emp.unique() if e and e.upper() not in _STATUS}
    per_token = {}
    flagged = set()
    for tok in _ABBR_TOKENS:
        rx = re.compile(rf"\b{tok}\b")
        hits = {e for e in distinct if rx.search(e.upper())}
        if hits:
            per_token[tok] = {"count": len(hits), "examples": _ex(hits, 5)}
            flagged |= hits
    return {"distinct_employers_flagged": len(flagged), "by_token": per_token}


def _emp_fp(name: str) -> str:
    # Keep single-letter tokens (they distinguish M&R from S&A); drop only legal
    # suffixes / connectors. Abbreviations normalized so MGMT≈MANAGEMENT collide.
    toks = [_ABBR_NORM.get(t, t) for t in re.findall(r"[A-Z0-9]+", name.upper())]
    toks = [t for t in toks if t not in _LEGAL]
    return " ".join(sorted(toks))


def scan_employer_near_duplicates(df: pd.DataFrame) -> dict:
    """Distinct employer strings that reduce to the same fingerprint (same firm
    written differently — abbreviation / punctuation / word-order / suffix)."""
    emp = df.loc[df["entity_type"] == "INDIVIDUAL", "contributor_employer"]
    groups = defaultdict(set)
    for e in emp.unique():
        if e and e.upper() not in _STATUS:
            fp = _emp_fp(e)
            if fp:
                groups[fp].add(e)
    dupes = {fp: g for fp, g in groups.items() if len(g) > 1}
    examples = [sorted(g) for g in sorted(dupes.values(), key=lambda g: -len(g))[:8]]
    return {"groups": len(dupes), "examples": examples}


def scan_name_composite_drift(df: pd.DataFrame) -> dict:
    """contributor_name that doesn't match the canonical "LAST, FIRST" rebuilt
    from first/last (the kind of drift the canonicalize pass closes)."""
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    first = ind["contributor_first_name"].fillna("").str.strip()
    last = ind["contributor_last_name"].fillna("").str.strip()
    rebuilt = (last + ", " + first).where((first != "") & (last != ""), last)
    drift = ind["contributor_name"].fillna("").str.strip() != rebuilt
    bad = ind[drift]
    return {"rows": int(drift.sum()), "donors": int(bad["donor_key"].nunique()),
            "examples": _ex({f"{n}  ≠  {r}" for n, r in zip(bad["contributor_name"].head(40), rebuilt[drift].head(40))}, 6)}


def _street_fp(s: str) -> str:
    toks = re.findall(r"[A-Z0-9]+", s.upper())
    return (toks[0] + "|" + " ".join(sorted(toks[1:]))) if toks else ""


def scan_address_order_variants(df: pd.DataFrame) -> dict:
    """Per donor: same ZIP + same token-set street written different ways."""
    ind = df[(df["entity_type"] == "INDIVIDUAL") & (df["contributor_street_1"] != "")]
    n_groups, examples = 0, []
    for (_, _z, _fp), g in ind.groupby(["donor_key", "contributor_zip",
                                        ind["contributor_street_1"].map(_street_fp)]):
        streets = g["contributor_street_1"].unique()
        if len(streets) > 1:
            n_groups += 1
            if len(examples) < 8:
                examples.append(sorted(streets))
    return {"groups": n_groups, "examples": examples}


# Real short occupations / credentials — a 2-5 char occupation is NOT junk just
# for being short. Without this guard the scanner below would flag every one.
_REAL_SHORT_OCCUPATIONS = {
    "CPA", "CEO", "CFO", "COO", "CTO", "CIO", "CMO", "MD", "DO", "DDS", "DMD",
    "RN", "LPN", "NP", "PA", "PT", "OT", "DVM", "ESQ", "JD", "PHD", "MBA",
    "VP", "EVP", "SVP", "GM", "HR", "IT", "PR", "RE", "SW", "UX", "QA",
    # Confirmed real on a monthly-pull review — vowel-less, so they trip the
    # heuristic below every run until listed. This whitelist is the cheap side
    # of the trade: real abbreviations are a FINITE, slow-growing set, whereas
    # junk is infinite (fresh keyboard mash every pull). Growing this list makes
    # the report converge; chasing junk in a denylist never would.
    "VFX", "CPT", "RSM", "CLO", "CGO", "CRO", "CHRO", "PM", "PMO", "SRE",
}
_VOWELS = set("AEIOUY")


def _is_rare_uncategorized(value, counts, occ, cat, uncategorized) -> bool:
    """Gate every junk heuristic: the value must be globally rare AND have had no
    categorisation rule match it. A common or categorised occupation is real —
    whatever any single donor's history looks like."""
    if not value or value in _STATUS or value in _REAL_SHORT_OCCUPATIONS:
        return False
    if int(counts.get(value, 0)) > 5:
        return False
    return bool((cat[occ == value].isin(uncategorized)).all())


def scan_junk_occupations(df: pd.DataFrame) -> dict:
    """Surface likely keyboard-mash / placeholder occupations for review.

    DETECT-ONLY on purpose. Structural junk (numbers, punctuation, XXXX) can be
    pattern-matched and auto-blanked — see JUNK_EMPLOYER_RE — but junk like
    'SDSDS' / 'FDE' / 'UGH' is an ordinary alphabetic token, structurally
    identical to a REAL abbreviation ('CPA', 'ESQ', 'RN'). No regex separates
    them, so these are reported for a human to confirm and add to the curated
    OCCUPATION_FIXES junk map — never blanked automatically.

    A value is suspicious when it is rare, uncategorized (no rule matched, so it
    fell through to OTHER / blank), and *looks* wrong: very short, vowel-less, or
    carrying a repeated-character run.
    """
    if "contributor_occupation" not in df.columns:
        return {"distinct": 0, "rows": 0, "examples": []}
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    occ = ind["contributor_occupation"].fillna("").astype(str).str.strip().str.upper()
    cat = ind.get("occupation_category", pd.Series("", index=ind.index)).fillna("").astype(str).str.upper()

    counts = occ.value_counts()
    uncategorized = {"", "OTHER"}
    suspects = {}
    for value, n_rows in counts.items():
        if not value or value in _STATUS or value in _REAL_SHORT_OCCUPATIONS:
            continue
        if n_rows > 5:                                   # common ⇒ almost surely real
            continue
        if not (cat[occ == value].isin(uncategorized)).all():
            continue                                     # a rule categorized it ⇒ real
        # Multi-word values are composed titles a person typed on purpose
        # ("SR PM", "SR. VP") — mash is a single blob. Structural, so it costs
        # no list maintenance.
        if " " in value:
            continue
        letters = [c for c in value if c.isalpha()]
        # High-precision signals only. "Short" alone was tried and is far too
        # loose — it flags BUYER / AUDIT / CLO / CGO, all real. Vowel-less is
        # the signal that actually separates mash (SDSDS, XXX, 3F) from real
        # abbreviations, which practically always carry one.
        #
        # Deliberately imperfect: this CANNOT catch word-shaped junk (FDE, UGH,
        # WHAT, ABC) — they are structurally identical to real abbreviations
        # (FDE vs VFX), so no rule separates them and a human still has to spot
        # those. The value here is narrowing ~1,000 uncategorized occupations to
        # a handful per pull, not perfect recall.
        looks_wrong = (
            not (set(letters) & _VOWELS)                 # no vowel at all
            or re.search(r"(.)\1{2,}", value)            # SSSZSS, XXXX
            or len(value) <= 2                           # 2-char, not whitelisted
        )
        if looks_wrong:
            suspects[value] = int(n_rows)

    # Cross-record evidence. A structural rule cannot see that 'FDE' is junk and
    # 'VFX' is real — but the DONOR's own filings can: someone who filed DOCTOR
    # 50 times and 'ZZZ' once did not change careers. This catches word-shaped
    # junk the signals above miss, and carries the evidence so the call takes a
    # second. NOT auto-applied: a lone odd occupation is sometimes a real job
    # change (50x TEACHER then 1x PRINCIPAL is a promotion, not junk), so this
    # informs the human instead of overwriting them.
    evidence = {}
    if "donor_key" in ind.columns:
        pair = ind.assign(_o=occ).groupby(["donor_key", "_o"]).size()
        per_donor = ind.groupby("donor_key").size()
        for (dk, value), n in pair.items():
            # Only ever judge a value that is ALREADY globally rare and
            # uncategorized. Without this guard the per-donor signal gets
            # generalised into a verdict on the whole value — one donor who
            # filed ATTORNEY once and DEVELOPER nine times would condemn all
            # 9,540 ATTORNEY rows. The donor history refines a shortlist; it
            # must never build one.
            if not _is_rare_uncategorized(value, counts, occ, cat, uncategorized):
                continue
            total = int(per_donor.get(dk, 0))
            # rare-for-this-donor AND the donor is otherwise well-established
            if n <= 2 and total >= 10 and (total - n) >= 8:
                others = sorted({o for o in occ[ind["donor_key"] == dk]
                                 if o and o != value and o not in _STATUS})
                if others:
                    evidence.setdefault(value, []).append(
                        f"{n}x vs {total - n}x {'/'.join(others[:2])}")

    merged = {v: {"rows": n, "why": "shape"} for v, n in suspects.items()}
    for v, ev in evidence.items():
        merged.setdefault(v, {"rows": int(counts.get(v, 0)), "why": ""})
        merged[v]["why"] = (merged[v]["why"] + "+donor-history").lstrip("+")
        merged[v]["evidence"] = ev[:2]
    return {
        "distinct": len(merged),
        "rows": int(sum(d["rows"] for d in merged.values())),
        "examples": [{"value": v, **d}
                     for v, d in sorted(merged.items(), key=lambda x: -x[1]["rows"])[:15]],
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
    ea = report["employer_abbreviations"]
    print(f"\n  Employer abbreviations: {ea['distinct_employers_flagged']:,} distinct employers")
    for tok, d in sorted(ea["by_token"].items(), key=lambda x: -x[1]["count"]):
        print(f"    {tok:8} {d['count']:>4}   e.g. {', '.join(d['examples'][:3])}")
    nd = report["employer_near_duplicates"]
    print(f"\n  Employer near-duplicates (same firm, diff text): {nd['groups']:,} groups")
    for g in nd["examples"][:5]:
        print(f"    • {'  |  '.join(g)}")
    cd = report["name_composite_drift"]
    print(f"\n  Name composite drift: {cd['rows']:,} rows / {cd['donors']:,} donors")
    for e in cd["examples"][:4]:
        print(f"    • {e}")
    av = report["address_order_variants"]
    print(f"\n  Address order/spacing variants: {av['groups']:,} per-donor groups")
    for g in av["examples"][:4]:
        print(f"    • {'  |  '.join(g)}")
    jo = report["junk_occupation_suspects"]
    print(f"\n  Junk occupation suspects: {jo['distinct']:,} values / {jo['rows']:,} rows"
          f"{'  — confirm, then add to OCCUPATION_FIXES (junk) or _REAL_SHORT_OCCUPATIONS (real)' if jo['distinct'] else ''}")
    for e in jo["examples"][:8]:
        print(f"    • {e['value']}  ({e['rows']} rows)")

    out = path.parent / "quality_scan.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
