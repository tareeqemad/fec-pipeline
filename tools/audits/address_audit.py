"""Address audit: raw vs cleaned, per sub_id (vectorised).

  1. Is every cleaned street / city / state / zip grounded in the donor's OWN raw filings?
     (a value from nowhere or from another person is the thing we must never do)
  2. Did a unification erase a genuine move: a different house/street, or a different apartment number?
Outputs data/_review/address_audit_flags.csv and address_audit_moves.csv, prints a summary.
"""
import re
import sys

import pandas as pd

ADDR = ["contributor_street_1", "contributor_street_2", "contributor_city", "contributor_state", "contributor_zip"]
_ABBR = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "LANE": "LN", "COURT": "CT",
    "PLACE": "PL", "CIRCLE": "CIR", "TERRACE": "TER", "PARKWAY": "PKWY", "HIGHWAY": "HWY", "TRAIL": "TRL",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW", "APARTMENT": "APT", "SUITE": "STE", "FLOOR": "FL", "SQUARE": "SQ",
    "POBOX": "PO BOX", "BUILDING": "BLDG", "NUMBER": "#", "MOUNT": "MT", "SAINT": "ST", "FORT": "FT",
}
_TYPES = {"ST", "AVE", "RD", "DR", "BLVD", "LN", "CT", "PL", "CIR", "TER", "PKWY", "HWY", "TRL", "SQ", "WAY", "LANDE",
          "LOOP", "RUN", "PT", "PATH", "PLZ", "XING", "ROW", "BND", "HTS"}
_DIRS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_UNIT_WORDS = r"APT|STE|UNIT|FL|RM|PH|BLDG|LOT|SPC|PMB"
_UNIT_RE = re.compile(rf"(?:\b({_UNIT_WORDS})\b|#)")
_UNIT_STRIP_RE = re.compile(rf"(?:\b({_UNIT_WORDS}|SUITE|FLOOR)\b|#)")
_ORD_RE = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")

# steps whose changes are dictionary / format normalisation of the row's OWN value (no history involved)
_FORMAT_STEPS = {"streets_normalize", "zips_normalize", "cities_normalize", "streets_safe_fixes", "streets_unify_units",
                 "streets_unify_spacing", "streets_unify_spelling", "address_review", "donor_canonical_units",
                 "donor_pobox_typos", "safety_fix_garbage_city_names"}


def norm(s) -> str:
    s = str(s or "").upper().replace("&", " AND ")
    s = re.sub(r"[.,;:'\"()]", " ", s)
    s = re.sub(r"#\s*", " # ", s)
    s = re.sub(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)", " ", s)      # E72 -> E 72, 3BEACH -> 3 BEACH
    s = " ".join(_ABBR.get(t, t) for t in s.split())
    s = _ORD_RE.sub(r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


def zip5(s) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    return d[:5] if len(d) >= 5 else d


def core_and_unit(street_1, street_2) -> tuple[str, str]:
    s, unit = norm(street_1), norm(street_2)
    m = _UNIT_RE.search(s)
    if m:
        unit = (s[m.start():] + " " + unit).strip()
        s = s[:m.start()].strip()
    return s, re.sub(r"\s+", "", _UNIT_STRIP_RE.sub(" ", unit))


def sig(core: str) -> tuple:
    """What a human would call 'the same address': house number + street words, ignoring type/direction/spacing."""
    t = core.split()
    if not t:
        return ("", "")
    house = re.sub(r"[^0-9]", "", t[0]) if re.match(r"^\d", t[0]) else t[0]
    words = [w for w in t[1:] if w not in _TYPES and w not in _DIRS]
    return (house, "".join(words))


def main():
    raw = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, low_memory=False,
                      usecols=["sub_id", *ADDR]).set_index("sub_id")
    cln = pd.read_csv("data/contributions_cleaned.csv", dtype=str, keep_default_na=False, low_memory=False,
                      usecols=["sub_id", "donor_key", "entity_type", "contributor_name", *ADDR]).set_index("sub_id")
    cln = cln[cln.entity_type == "INDIVIDUAL"]
    raw = raw.loc[cln.index]
    chg = pd.read_csv("data/audit_changes.csv", dtype=str, keep_default_na=False, low_memory=False, usecols=["sub_id", "field", "step"])
    chg = chg[chg.field.isin(ADDR) & chg.sub_id.isin(cln.index)]
    steps_of = chg.groupby("sub_id").step.agg(lambda s: ", ".join(sorted(set(s))))
    history_steps = chg[~chg.step.isin(_FORMAT_STEPS)].groupby("sub_id").step.agg(lambda s: ", ".join(sorted(set(s))))
    print(f"individual rows: {len(cln):,} | rows with any address change: {len(steps_of):,} | rows changed by a history/rule step: {len(history_steps):,}")

    t = pd.DataFrame(index=cln.index)
    t["key"] = cln.donor_key
    t["name"] = cln.contributor_name
    t["r_core"], t["r_unit"] = zip(*[core_and_unit(a, b) for a, b in zip(raw.contributor_street_1, raw.contributor_street_2)])
    t["c_core"], t["c_unit"] = zip(*[core_and_unit(a, b) for a, b in zip(cln.contributor_street_1, cln.contributor_street_2)])
    t["r_sig"] = t.r_core.map(sig); t["c_sig"] = t.c_core.map(sig)
    t["r_contributor_city"] = [norm(v) for v in raw.contributor_city]; t["c_contributor_city"] = [norm(v) for v in cln.contributor_city]
    t["r_contributor_state"] = [norm(v) for v in raw.contributor_state]; t["c_contributor_state"] = [norm(v) for v in cln.contributor_state]
    t["r_contributor_zip"] = [zip5(v) for v in raw.contributor_zip]; t["c_contributor_zip"] = [zip5(v) for v in cln.contributor_zip]

    own = {}
    for col in ["r_sig", "r_contributor_city", "r_contributor_state", "r_contributor_zip"]:
        own[col] = t[t[col].astype(str) != ""].groupby("key")[col].agg(set)
    owners = t[(t.r_core != "") & (t.r_contributor_zip != "")].groupby(["r_sig", "r_contributor_zip"]).key.agg(set)
    surname = t.name.str.split(",").str[0].str.strip().str.upper()
    surname_of_key = surname.groupby(t.key).agg(lambda s: s.mode().iloc[0])

    flags = []
    for sid, row in t.loc[steps_of.index].iterrows():
        k, steps = row.key, steps_of[sid]
        kind = "history" if sid in history_steps.index else "format"
        if row.c_sig != row.r_sig and row.c_core:
            if row.c_sig not in own["r_sig"].get(k, set()):
                others = owners.get((row.c_sig, row.c_contributor_zip), set()) - {k}
                flags.append((sid, k, row["name"], "street_not_in_donor_raw_history", kind, steps,
                              f"{raw.at[sid, 'contributor_street_1']} | {raw.at[sid, 'contributor_street_2']}",
                              f"{cln.at[sid, 'contributor_street_1']} | {cln.at[sid, 'contributor_street_2']}",
                              "; ".join(sorted({surname_of_key.get(o, '') for o in others}))))
        if row.r_unit and row.c_unit and row.r_unit != row.c_unit:
            flags.append((sid, k, row["name"], "unit_number_changed", kind, steps,
                          f"{raw.at[sid, 'contributor_street_1']} | {raw.at[sid, 'contributor_street_2']}",
                          f"{cln.at[sid, 'contributor_street_1']} | {cln.at[sid, 'contributor_street_2']}", ""))
        for c in ["contributor_city", "contributor_state", "contributor_zip"]:
            fv, rv = row["c_" + c], row["r_" + c]
            if fv and fv != rv and fv not in own["r_" + c].get(k, set()):
                flags.append((sid, k, row["name"], f"{c}_not_in_donor_raw_history", kind, steps, raw.at[sid, c], cln.at[sid, c], ""))
    flags = pd.DataFrame(flags, columns=["sub_id", "donor_key", "name", "flag", "kind", "steps", "raw", "cleaned", "other_donors_filing_this_address"])
    flags.to_csv("data/_review/address_audit_flags.csv", index=False)
    print("\n== row-level flags (final value NOT found in the donor's own raw filings), by kind of step:")
    print(flags.groupby(["kind", "flag"]).size().to_string() if len(flags) else "   none")
    print("\n== HISTORY/RULE-step flags in full (these are the ones that could be another person's address):")
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 70); pd.set_option("display.max_rows", 200)
    h = flags[flags.kind == "history"]
    print(h[["name", "flag", "steps", "raw", "cleaned", "other_donors_filing_this_address"]].to_string(index=False) if len(h) else "   none")
    f = flags[(flags.kind == "format") & (flags.flag != "contributor_zip_not_in_donor_raw_history")]
    print(f"\n== FORMAT-step flags other than ZIP (sample of {min(30, len(f))} of {len(f)}):")
    print(f.sample(min(30, len(f)), random_state=1)[["name", "flag", "steps", "raw", "cleaned"]].to_string(index=False) if len(f) else "   none")

    moves = []
    for k, d in t.groupby("key"):
        raw_sigs = {s for s, c in zip(d.r_sig, d.r_core) if c and c[0].isdigit() and s[1]}
        cln_sigs = {s for s, c in zip(d.c_sig, d.c_core) if c}
        lost = sorted(raw_sigs - cln_sigs)
        if lost:
            moves.append((k, d["name"].iloc[0], len(d), "addresses: " + " | ".join(f"{h} {w}" for h, w in lost),
                          " | ".join(sorted({c for c in d.c_core if c}))))
        for s in cln_sigs:
            r_units = set(d.loc[d.r_sig == s, "r_unit"]) - {""}
            c_units = set(d.loc[d.c_sig == s, "c_unit"]) - {""}
            if len(r_units) > 1 and len(r_units) > len(c_units):
                moves.append((k, d["name"].iloc[0], len(d), f"units at {s[0]} {s[1]}: raw {sorted(r_units)}", f"cleaned {sorted(c_units)}"))
    moves = pd.DataFrame(moves, columns=["donor_key", "name", "rows", "raw_addresses_lost", "cleaned_addresses"])
    moves.to_csv("data/_review/address_audit_moves.csv", index=False)
    print(f"\n== donors whose distinct raw addresses / apartment numbers were reduced beyond cosmetic variants: {len(moves)}")
    if len(moves):
        print(moves.to_string(index=False))


if __name__ == "__main__":
    sys.exit(main())
