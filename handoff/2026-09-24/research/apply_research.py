"""Merge researcher + checker results into data/manual_employer_addresses.csv.

python apply_research.py [dry|apply]
- a checker's 'no' replaces the researcher's row; rows noted 'UNCHECKED' are skipped (left for a later session)
- correct/corrected -> the office with its source; home_based -> town only; the rest -> 'INVALID: ...'
- checked business premises (home_check.csv) get the 'OFFICE PREMISES:' note so their street stays
"""
import glob
import re
import sys
from pathlib import Path

import pandas as pd

R = Path(__file__).resolve().parent
REPO = Path("/home/user/fec-pipeline")
MANUAL = REPO / "data" / "manual_employer_addresses.csv"
TODAY = "2026-09-24"
kw = dict(dtype=str, keep_default_na=False, na_values=[])
FIELDS = ["verdict", "organisation", "address", "city", "state", "zip", "source_name", "source_url", "note"]
# words manual_overrides reads as 'keep in review' (fec/resolve/pipeline/manual_overrides.py)
MARKERS = {"VERIFIED": "confirmed", "VERIFY": "confirm", "LIKELY": "probably",
           "UNCERTAIN": "unclear", "MEDIUM": "moderate"}


def neutral(text: str) -> str:
    for word, plain in MARKERS.items():
        text = re.sub(rf"\b{word}\b", plain, text, flags=re.IGNORECASE)
    return " ".join(text.split())


targets = pd.read_csv(REPO / "handoff/2026-09-24/research_targets.csv", **kw).set_index("rid")
research = [pd.read_csv(REPO / "handoff/2026-09-24/research_rid_1-234_unchecked.csv", **kw)]
research += [pd.read_csv(p, **kw) for p in sorted(glob.glob(str(R / "rid_*-*.csv")))
             if re.search(r"rid_\d+-\d+\.csv$", p) and Path(p).name != "rid_331-378.csv"]
research = pd.concat(research).drop_duplicates("rid", keep="last").set_index("rid")
checks = pd.concat([pd.read_csv(p, **kw) for p in sorted(glob.glob(str(R / "check_*-*.csv")))])
checks = checks.drop_duplicates("rid", keep="last").set_index("rid")

final, skipped, overruled = {}, [], 0
for rid, row in research.iterrows():
    chosen = row[FIELDS].to_dict()
    if rid in checks.index and checks.at[rid, "agree"].strip().lower() == "no":
        chosen = checks.loc[rid, FIELDS].to_dict()
        overruled += 1
    if "UNCHECKED" in chosen["note"].upper():
        skipped.append(rid)
        continue
    final[rid] = chosen

missing = sorted(set(targets.index) - set(research.index), key=int)
rows = []
for rid, r in final.items():
    name = targets.at[rid, "employer_name"].strip().upper()
    verdict = r["verdict"].strip()
    note = neutral(r["note"])
    base = {"name": name, "is_primary": "true", "source_name": r["source_name"], "source_url": r["source_url"]}
    if verdict in ("correct", "corrected"):
        if not (r["address"].strip() and r["city"].strip() and r["state"].strip() and r["source_url"].strip()):
            skipped.append(rid)
            continue
        rows.append({**base, "address": r["address"].strip(), "city": r["city"].strip(),
                     "state": r["state"].strip().upper(), "zip": r["zip"].strip(),
                     "note": f"{r['organisation'].strip()}: {note} (researched and checked {TODAY})"})
    elif verdict == "home_based":
        rows.append({**base, "address": "", "city": r["city"].strip(), "state": r["state"].strip().upper(),
                     "zip": r["zip"].strip(),
                     "note": f"Home-based business, town only: {note} (researched and checked {TODAY})"})
    else:
        rows.append({**base, "address": "", "city": "", "state": "", "zip": "",
                     "note": f"INVALID: {verdict}: {note} (researched and checked {TODAY})"})
new = pd.DataFrame(rows)

# donor names never go into a note: any surname of the employer's donors blocks the apply
by_name = {targets.at[rid, "employer_name"].strip().upper(): targets.at[rid, "donor_names"] for rid in final}
named = []
for _, row in new.iterrows():
    surnames = {part.split(",")[0].strip().upper() for part in by_name.get(row["name"], "").split(";")}
    words = set(re.findall(r"[A-Z][A-Z'\-]+", row["note"].upper()))
    hit = {s for s in surnames if len(s) > 2 and s in words and s not in row["name"]}
    if hit:
        named.append((row["name"], sorted(hit)))
print("notes naming a donor (free text dropped):", len(named), named)
organisation = {targets.at[rid, "employer_name"].strip().upper(): research.at[rid, "organisation"] for rid in final}
for name, _hit in named:
    index = new.index[new["name"].eq(name)][0]
    kind = re.match(r"^(INVALID: \w+|Home-based business, town only)", new.at[index, "note"])
    head = kind.group(1) + ": " if kind else ""
    label = organisation[name].strip()
    if any(s in set(re.findall(r"[A-Z][A-Z'\-]+", label.upper())) for s in _hit if s not in name):
        label = name  # the organisation's description names a donor: the employer name alone
    new.at[index, "note"] = f"{head}{label} (researched and checked {TODAY})"

manual = pd.read_csv(MANUAL, **kw)
manual_names = manual["name"].str.strip().str.upper()
replaced = int(manual_names.isin(set(new["name"])).sum())
manual = manual[~manual_names.isin(set(new["name"]))]
manual = pd.concat([manual, new[manual.columns]], ignore_index=True)

# checked business premises keep their street
premises = []
check_path = R / "home_check.csv"
if check_path.exists():
    home = pd.read_csv(R / "home_candidates.csv", **kw).set_index("hid")
    for _, c in pd.read_csv(check_path, **kw).iterrows():
        if c["final_verdict"].strip() != "premises":
            continue
        cand = home.loc[c["hid"]]
        name = cand["employer_name"].strip().upper()
        mask = manual["name"].str.strip().str.upper().eq(name) & manual["address"].str.strip().ne("")
        prefix = "OFFICE PREMISES: "
        if mask.any():
            manual.loc[mask, "note"] = manual.loc[mask, "note"].map(
                lambda n: n if n.upper().startswith(prefix.strip()) else prefix + n)
        else:
            manual.loc[len(manual)] = {
                "name": name, "address": cand["employer_address"], "city": cand["employer_city"],
                "state": cand["employer_state"], "zip": cand["employer_zip"], "is_primary": "true",
                "note": f"{prefix}{neutral(c['reason'])} (checked {TODAY})",
                "source_name": "business listing", "source_url": c["source_url"]}
        premises.append(name)

print(f"research rows {len(research)} | checker overruled {overruled} | skipped (UNCHECKED/incomplete) {len(skipped)}: {skipped}")
print(f"targets without research {len(missing)}: {missing[:20]}{'...' if len(missing) > 20 else ''}")
print("new manual rows by kind:", new["note"].str.extract(r"^(INVALID|Home-based)")[0].fillna("office").value_counts().to_dict())
print(f"existing manual rows replaced {replaced} | premises marked {len(premises)} | manual rows {len(manual)}")
if (sys.argv[1:] or ["dry"])[0] == "apply":
    raw = MANUAL.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    manual.to_csv(MANUAL, index=False, lineterminator=newline)
    print("written", MANUAL)
