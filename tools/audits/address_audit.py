"""Address audit: raw vs cleaned, per sub_id (vectorised).

  1. Is every cleaned street / city / state / zip grounded in the donor's OWN raw filings?
     (a value from nowhere or from another person is the thing we must never do)
  2. Did a unification erase a genuine move: a different house/street, or a different apartment number?
  3. ZIP check, every entity type and every step (format steps included): a cleaned city that
     differs from the filed one and that NO other filing pairs with the row's ZIP5 (EAST HARTFORD
     -> WEST HARTFORD at 06128; MANHATTAN BEACH -> LOS ANGELES at 90266). The donor-history test
     cannot see these when the donor filed the new city elsewhere, and a dictionary/fuzzy city fix
     is a "format" step, so they are listed in full, never sampled, and make the audit exit 1.
     A pure abbreviation expansion (MOUNTAIN BRK -> MOUNTAIN BROOK, MAYFIELD HTS -> HEIGHTS) is the
     same name and is not a contradiction; filings of the abbreviated form count as support.
Outputs <out-dir>/address_audit_flags.csv, address_audit_moves.csv and address_audit_zip_city.csv
(default out-dir data/_review), prints a summary.
Usage: python tools/audits/address_audit.py [--out-dir DIR]
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

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


# --- ZIP check -------------------------------------------------------------------------------
_VOWELS = frozenset("AEIOU")
ZIP_FLAG = "contributor_city_not_filed_with_zip"
# Same-place renames reviewed against USPS: the target spelling is right although no other filing
# in this pull pairs it with the row's ZIP. Keyed (state, filed city, cleaned city) after norm(), or
# (state, filed city, cleaned city, zip5) when the answer depends on the ZIP. Listed (not hidden) but
# do not fail the audit.
REVIEWED_SAME_PLACE = {
    ("SC", "HILTON HEAD", "HILTON HEAD ISLAND"),   # the town's name; 29926/29928 read HILTON HEAD ISLAND
    ("UT", "SLC", "SALT LAKE CITY"),               # 841xx: Salt Lake City
    ("NY", "NY", "BROOKLYN", "11239"),             # 11239 is a Brooklyn ZIP (the row's street field reads BROOKLYN)
}


def _reviewed(state: str, raw_city: str, cleaned_city: str, z: str) -> bool:
    return (state, raw_city, cleaned_city) in REVIEWED_SAME_PLACE or (state, raw_city, cleaned_city, z) in REVIEWED_SAME_PLACE


def _abbreviates(short: str, full: str) -> bool:
    """BRK/BROOK, HTS/HEIGHTS, SPGS/SPRINGS: a vowel-less short form of one word, letters in order.
    Direction letters never abbreviate anything (N is not NEW, E is not W)."""
    if short == full:
        return True
    if len(short) < 2 or len(short) >= len(full) or short[0] != full[0] or short in _DIRS:
        return False
    if _VOWELS.intersection(short):
        return False
    rest = iter(full)
    return all(ch in rest for ch in short)


def same_city_name(a: str, b: str) -> bool:
    """Same normalised city name, word by word, allowing vowel-less abbreviations of a word."""
    ta, tb = a.split(), b.split()
    return len(ta) == len(tb) and all(_abbreviates(x, y) or _abbreviates(y, x) for x, y in zip(ta, tb))


def zip_city_contradictions(raw: pd.DataFrame, cln: pd.DataFrame, steps: pd.Series | None = None) -> pd.DataFrame:
    """Rows (every entity type) whose cleaned city differs from the filed city and that no other filing pairs
    with the row's cleaned ZIP5. ``raw`` = every raw filing (the evidence), ``cln`` = cleaned rows; both indexed
    by sub_id. The row's own filing never supports it (its filed city is a different name by construction)."""
    filed = defaultdict(Counter)
    for city, z in zip(raw.contributor_city.map(norm), raw.contributor_zip.map(zip5)):
        if city and len(z) == 5:
            filed[z][city] += 1
    r_city = raw.contributor_city.reindex(cln.index).fillna("").map(norm)
    r_zip = raw.contributor_zip.reindex(cln.index).fillna("").map(zip5)
    c_city = cln.contributor_city.map(norm)
    c_zip = cln.contributor_zip.map(zip5)
    rows = []
    for sid, rc, rz, cc, z in zip(cln.index, r_city, r_zip, c_city, c_zip):
        if not cc or len(z) != 5 or same_city_name(cc, rc):
            continue
        at_zip = filed.get(z, Counter())
        if any(same_city_name(city, cc) for city in at_zip):
            continue
        others = at_zip.copy()
        if rc and rz == z:
            others[rc] -= 1                       # the row's own filing is not "another" filing
        others = +others
        rows.append((sid, rc, cc, z, "; ".join(f"{c} x{n}" for c, n in others.most_common(3)) or "(no other filing)"))
    out = pd.DataFrame(rows, columns=["sub_id", "raw_city_norm", "cleaned_city_norm", "zip5", "cities_filed_at_zip"]).set_index("sub_id")
    cols = [c for c in ("donor_key", "entity_type", "contributor_name", "contributor_state") if c in cln.columns]
    out = cln.loc[out.index, cols].join(out)
    out.insert(len(cols), "raw_city", raw.contributor_city.reindex(out.index))
    out.insert(len(cols) + 1, "cleaned_city", cln.contributor_city.reindex(out.index))
    out["steps"] = (steps.reindex(out.index).fillna("") if steps is not None else "")
    state = out["contributor_state"] if "contributor_state" in out.columns else pd.Series("", index=out.index)
    out["reviewed_same_place"] = [
        _reviewed(s, r, c, z) for s, r, c, z in zip(state, out.raw_city_norm, out.cleaned_city_norm, out.zip5)
    ]
    return out


def abbreviation_expansions(raw: pd.DataFrame, cln: pd.DataFrame) -> pd.Series:
    """City changes accepted as the same name (MOUNTAIN BRK -> MOUNTAIN BROOK): distinct mapping -> rows."""
    r_city = raw.contributor_city.reindex(cln.index).fillna("").map(norm)
    c_city = cln.contributor_city.map(norm)
    pairs = [(r, c) for r, c in zip(r_city, c_city) if r and c and r != c and same_city_name(c, r)]
    return pd.Series(Counter(f"{r} -> {c}" for r, c in pairs), dtype=int).sort_values(ascending=False)


def _print_zip_check(z: pd.DataFrame, expansions: pd.Series) -> int:
    open_rows = z[~z.reviewed_same_place]
    print("\n" + "!" * 100)
    print(f"!! ZIP CHECK: {len(open_rows):,} row(s) carry a cleaned city that no other filing pairs with their ZIP5"
          f" (+{int(z.reviewed_same_place.sum()):,} reviewed same-place renames) -- ALL listed, never sampled:")
    print("!" * 100)
    if len(z):
        grouped = (z.assign(n=1).groupby(["raw_city", "cleaned_city", "zip5", "steps", "reviewed_same_place", "cities_filed_at_zip"])
                   .n.sum().reset_index().sort_values(["reviewed_same_place", "n"], ascending=[True, False]))
        print("   by change:")
        print(grouped.to_string(index=False))
        print("   every row:")
        print(z.drop(columns=["raw_city_norm", "cleaned_city_norm"]).to_string())
    else:
        print("   none")
    if len(expansions):
        print(f"   (accepted as the same name, not flagged: {int(expansions.sum()):,} rows of pure abbreviation expansion: "
              + "; ".join(f"{k} x{v}" for k, v in expansions.items()) + ")")
    return len(open_rows)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Raw-vs-cleaned address audit (read-only on data/).")
    parser.add_argument("--out-dir", default="data/_review", help="where the audit CSVs go (default data/_review)")
    return parser.parse_args(argv)


def main(argv=None):
    out_dir = Path(_parse_args(argv).out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_all = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, na_values=[], low_memory=False,
                          usecols=["sub_id", *ADDR]).set_index("sub_id")
    cln_all = pd.read_csv("data/contributions_cleaned.csv", dtype=str, keep_default_na=False, na_values=[], low_memory=False,
                          usecols=["sub_id", "donor_key", "entity_type", "contributor_name", *ADDR]).set_index("sub_id")
    cln = cln_all[cln_all.entity_type == "INDIVIDUAL"]
    raw = raw_all.loc[cln.index]
    chg_all = pd.read_csv("data/audit_changes.csv", dtype=str, keep_default_na=False, na_values=[], low_memory=False, usecols=["sub_id", "field", "step"])
    chg_all = chg_all[chg_all.field.isin(ADDR)]
    chg = chg_all[chg_all.sub_id.isin(cln.index)]
    steps_of = chg.groupby("sub_id").step.agg(lambda s: ", ".join(sorted(set(s))))
    history_steps = chg[~chg.step.isin(_FORMAT_STEPS)].groupby("sub_id").step.agg(lambda s: ", ".join(sorted(set(s))))
    print(f"individual rows: {len(cln):,} | rows with any address change: {len(steps_of):,} | rows changed by a history/rule step: {len(history_steps):,}")

    city_steps = chg_all[chg_all.field.isin(["contributor_city", "contributor_zip"])].groupby("sub_id").step.agg(
        lambda s: ", ".join(sorted(set(s))))
    zipcheck = zip_city_contradictions(raw_all, cln_all, city_steps)
    n_zip_open = int((~zipcheck.reviewed_same_place).sum())
    print(f"!! ZIP CHECK (all entity types): {n_zip_open:,} cleaned cities not filed with their ZIP5 by anyone else"
          " -- listed in full at the end")

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
    # the ZIP check covers every entity type; its kind is informative only, it is never sampled
    history_all = set(chg_all.loc[~chg_all.step.isin(_FORMAT_STEPS), "sub_id"])
    for sid, z in zipcheck.iterrows():
        flags.append((sid, z.get("donor_key", ""), z.get("contributor_name", ""), ZIP_FLAG,
                      "history" if sid in history_all else "format", z.steps,
                      f"{z.raw_city} {z.zip5}", f"{z.cleaned_city} {z.zip5}",
                      ("REVIEWED same place; " if z.reviewed_same_place else "") + f"filed at this ZIP: {z.cities_filed_at_zip}"))
    flags = pd.DataFrame(flags, columns=["sub_id", "donor_key", "name", "flag", "kind", "steps", "raw", "cleaned", "other_donors_filing_this_address"])
    flags.to_csv(out_dir / "address_audit_flags.csv", index=False)
    zipcheck.to_csv(out_dir / "address_audit_zip_city.csv")
    print("\n== row-level flags (final value NOT found in the donor's own raw filings; ZIP check = all entity types), by kind of step:")
    print(flags.groupby(["kind", "flag"]).size().to_string() if len(flags) else "   none")
    print("\n== HISTORY/RULE-step flags in full (these are the ones that could be another person's address):")
    pd.set_option("display.width", 260); pd.set_option("display.max_colwidth", 70); pd.set_option("display.max_rows", 1000)
    h = flags[(flags.kind == "history") & (flags.flag != ZIP_FLAG)]
    print(h[["name", "flag", "steps", "raw", "cleaned", "other_donors_filing_this_address"]].to_string(index=False) if len(h) else "   none")
    f = flags[(flags.kind == "format") & ~flags.flag.isin(["contributor_zip_not_in_donor_raw_history", ZIP_FLAG])]
    print(f"\n== FORMAT-step flags other than ZIP (sample of {min(30, len(f))} of {len(f)}; ZIP-contradicted cities are listed in full at the end):")
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
    moves.to_csv(out_dir / "address_audit_moves.csv", index=False)
    print(f"\n== donors whose distinct raw addresses / apartment numbers were reduced beyond cosmetic variants: {len(moves)}")
    if len(moves):
        print(moves.to_string(index=False))

    n_open = _print_zip_check(zipcheck, abbreviation_expansions(raw_all, cln_all))
    if n_open:
        print(f"\nFAIL: {n_open:,} ZIP-contradicted city change(s) above need a fix or a reviewed same-place entry.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
