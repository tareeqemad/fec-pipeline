"""Name audit: raw vs cleaned per sub_id, plus per-donor identity check.

  1. Row level: is the cleaned surname / first name explainable from the SAME donor's raw filings
     (exact, punctuation/suffix/title variant, initial, or a nickname of a raw first name)?
     Anything else = the row now carries a name nobody filed for this donor.
  2. Donor level: does one donor_key hold two surnames that are not variants, or two first names
     that are not nickname/initial variants (two people under one key)?
Outputs data/_review/name_audit_flags.csv and name_audit_donors.csv.
"""
import re
import sys
from difflib import SequenceMatcher

import pandas as pd

sys.path.insert(0, ".")
from fec.donor_match.constants import NICKNAME_MAP  # noqa: E402

_SUFFIX = {"JR", "SR", "II", "III", "IV", "V", "MD", "PHD", "ESQ", "DDS", "CPA", "DO", "DVM", "JD", "RN", "PE", "MBA"}
_TITLE = {"DR", "MR", "MRS", "MS", "MISS", "RABBI", "HON", "REV", "SIR", "DAME", "PROF", "CAPT", "COL", "GEN", "SEN", "REP"}


def canon(first: str) -> str:
    f = first.upper()
    return NICKNAME_MAP.get(f, f)


def parse(name: str) -> tuple[str, str, list[str]]:
    """'LAST, FIRST M' -> (last, first, other tokens), punctuation/titles/suffixes removed."""
    s = re.sub(r"[.\'\"`]", "", str(name or "").upper())
    s = re.sub(r"[-/]", " ", s)
    last, _, rest = s.partition(",")
    ltoks = [t for t in last.split() if t not in _SUFFIX and t not in _TITLE]
    rtoks = [t for t in rest.split() if t not in _SUFFIX and t not in _TITLE]
    if not rtoks and not _ and len(ltoks) >= 2:           # "FIRST LAST" with no comma
        rtoks, ltoks = ltoks[:-1], ltoks[-1:]
    return " ".join(ltoks), (rtoks[0] if rtoks else ""), rtoks[1:]


def surname_variant(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return True
    a2, b2 = a.replace(" ", ""), b.replace(" ", "")
    if a2 == b2 or a2 in b2 or b2 in a2:                    # compound / hyphenated / maiden forms
        return True
    if set(a.split()) & set(b.split()):
        return True
    return SequenceMatcher(None, a2, b2).ratio() >= 0.85 and min(len(a2), len(b2)) >= 4


def first_variant(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return True
    ca, cb = canon(a), canon(b)
    if ca == cb:
        return True
    if len(a) == 1 or len(b) == 1:                           # initial
        return a[0] == b[0]
    if a.startswith(b) or b.startswith(a):                   # ROB / ROBERT, ALEX / ALEXANDER
        return True
    return SequenceMatcher(None, a, b).ratio() >= 0.8 and a[0] == b[0]


def main():
    raw = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, low_memory=False,
                      usecols=["sub_id", "contributor_name"]).set_index("sub_id")
    cln = pd.read_csv("data/contributions_cleaned.csv", dtype=str, keep_default_na=False, low_memory=False,
                      usecols=["sub_id", "donor_key", "entity_type", "contributor_name"]).set_index("sub_id")
    cln = cln[cln.entity_type == "INDIVIDUAL"]
    raw = raw.loc[cln.index]
    chg = pd.read_csv("data/audit_changes.csv", dtype=str, keep_default_na=False, low_memory=False, usecols=["sub_id", "field", "step"])
    chg = chg[chg.field.str.contains("name") & chg.sub_id.isin(cln.index)]
    steps_of = chg.groupby("sub_id").step.agg(lambda s: ", ".join(sorted(set(s))))

    t = pd.DataFrame(index=cln.index)
    t["key"] = cln.donor_key
    rp = [parse(n) for n in raw.contributor_name]
    cp = [parse(n) for n in cln.contributor_name]
    t["r_last"], t["r_first"], t["r_rest"] = zip(*rp)
    t["c_last"], t["c_first"], t["c_rest"] = zip(*cp)
    print(f"individual rows: {len(cln):,} | rows whose name changed: {int((raw.contributor_name != cln.contributor_name).sum()):,} | rows touched by a name step: {len(steps_of):,}")

    own_last = t[t.r_last != ""].groupby("key").r_last.agg(set)
    own_first = t[t.r_first != ""].groupby("key").r_first.agg(set)

    flags = []
    changed = t[(raw.contributor_name.str.upper() != cln.contributor_name.str.upper()).to_numpy()]
    for sid, row in changed.iterrows():
        k = row.key
        steps = steps_of.get(sid, "")
        if row.c_last and not any(surname_variant(row.c_last, l) for l in own_last.get(k, set())):
            flags.append((sid, k, "surname_not_filed_by_this_donor", steps, raw.at[sid, "contributor_name"], cln.at[sid, "contributor_name"]))
        elif row.c_last and not surname_variant(row.c_last, row.r_last):
            flags.append((sid, k, "surname_changed_to_another_of_donors_own", steps, raw.at[sid, "contributor_name"], cln.at[sid, "contributor_name"]))
        if row.c_first and not any(first_variant(row.c_first, f) for f in own_first.get(k, set())):
            flags.append((sid, k, "first_name_not_filed_by_this_donor", steps, raw.at[sid, "contributor_name"], cln.at[sid, "contributor_name"]))
        elif row.c_first and not first_variant(row.c_first, row.r_first):
            flags.append((sid, k, "first_name_changed_to_another_of_donors_own", steps, raw.at[sid, "contributor_name"], cln.at[sid, "contributor_name"]))
    flags = pd.DataFrame(flags, columns=["sub_id", "donor_key", "flag", "steps", "raw_name", "cleaned_name"])
    flags.to_csv("data/_review/name_audit_flags.csv", index=False)
    print("\n== row-level flags:")
    print(flags.groupby(["flag", "steps"]).size().sort_values(ascending=False).to_string() if len(flags) else "   none")
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 60); pd.set_option("display.max_rows", 300)
    hard = flags[flags.flag.str.endswith("not_filed_by_this_donor")]
    print(f"\n== names NOT filed by this donor at all ({len(hard)} rows, {hard.donor_key.nunique()} donors) — all of them:")
    print(hard.drop_duplicates(["donor_key", "raw_name", "cleaned_name"])[["flag", "steps", "raw_name", "cleaned_name"]].to_string(index=False) if len(hard) else "   none")

    # donor level: two people under one key?
    donors = []
    for k, d in t.groupby("key"):
        lasts = sorted({l for l in d.r_last if l})
        firsts = sorted({f for f in d.r_first if f and len(f) > 1})
        bad_last = [(a, b) for i, a in enumerate(lasts) for b in lasts[i + 1:] if not surname_variant(a, b)]
        bad_first = [(a, b) for i, a in enumerate(firsts) for b in firsts[i + 1:] if not first_variant(a, b)]
        if bad_last or bad_first:
            donors.append((k, cln.loc[d.index[0], "contributor_name"], len(d),
                           " | ".join(sorted(set(raw.loc[d.index, "contributor_name"]))),
                           "; ".join(f"{a}/{b}" for a, b in bad_last), "; ".join(f"{a}/{b}" for a, b in bad_first)))
    donors = pd.DataFrame(donors, columns=["donor_key", "cleaned_name", "rows", "raw_names", "surname_conflicts", "first_name_conflicts"])
    donors.to_csv("data/_review/name_audit_donors.csv", index=False)
    print(f"\n== donor keys holding raw names that are not variants of each other: {len(donors)}")
    print(donors[["cleaned_name", "rows", "raw_names", "surname_conflicts", "first_name_conflicts"]].to_string(index=False) if len(donors) else "   none")


if __name__ == "__main__":
    sys.exit(main())
