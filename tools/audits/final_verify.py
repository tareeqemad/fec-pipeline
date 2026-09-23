"""Final handover verification of data/contributions_cleaned.csv and its companions."""
import csv
import json
import os

import pandas as pd

S = "tools/audits"
n = pd.read_csv("data/contributions_cleaned.csv", dtype=str, keep_default_na=False, low_memory=False)
n["amt"] = pd.to_numeric(n.contribution_receipt_amount, errors="coerce").fillna(0)
raw = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, usecols=["sub_id", "committee_id", "contribution_receipt_date"], low_memory=False)
print("== rows: cleaned", len(n), "| raw", len(raw), "| same sub_ids:", set(n.sub_id) == set(raw.sub_id), "| dup sub_id:", int(n.sub_id.duplicated().sum()))
d = pd.to_datetime(raw.contribution_receipt_date, errors="coerce")
print("== latest date per committee:", raw.assign(d=d).groupby("committee_id").d.max().dt.date.to_dict())

q = json.load(open("data/quality_gates.json"))
print("== quality gates passed:", q["passed"], "| issues:", q["issues"])
a = json.load(open("data/audit_summary.json"))
print("== audit: changes", a["changes"], "| untracked", a["untracked_changes"])
for step in ["enh_normalize_occupation_style", "enh_occupation_typo_fixes", "enh_employer_synonyms_final", "foreign_address_restore",
             "safety_disambiguate_vague_occupation", "streets_trim_truncated", "donor_converge_occupation_within_employer"]:
    print(f"   {step}: {a['steps'].get(step, {}).get('changes')}")
print("   summary total == sum of steps:", a["changes"] == sum(v["changes"] for v in a["steps"].values()))
for occ in ["DEVELOPER", "REAL ESTATE DEVELOPER", "SOFTWARE DEVELOPER"]:
    print(f"   {occ}: {int((n.contributor_occupation == occ).sum())} rows")
smith = n[n.donor_key == "34befe88f597"]
print("   SMITH, ARTHUR occupations:", smith.contributor_occupation.value_counts().to_dict())
neub = n[n.contributor_name.str.startswith("NEUBERGER, YEHUDA")].sort_values("contribution_receipt_date").iloc[-1]
print("   NEUBERGER latest:", neub.contributor_street_1, "|", neub.contributor_street_2, "|", neub.latitude, neub.longitude)
import subprocess, sys
chk = subprocess.run([sys.executable, "sync_rosters.py", "--check"], capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8"})
print("== roster sync --check exit:", chk.returncode, "|", chk.stdout.strip().splitlines()[0] if chk.stdout.strip() else chk.stderr[-300:])

# donor identity rules all applied
rules = list(csv.DictReader(open("data/database/donor_identity_rules.csv", encoding="utf-8-sig", newline="")))
merges = [r for r in rules if r["action"].lower() == "merge_keys"]
keys = set(n.donor_key)
dropped_present = [r["donor_key_b"] for r in merges if r["donor_key_b"] in keys]
keep_missing = [r["donor_key_a"] for r in merges if r["donor_key_a"] not in keys]
print("== identity rules:", len(rules), "| merges:", len(merges), "| dropped keys still present:", len(dropped_present), "| keep keys absent:", len(keep_missing))
ind = n[n.entity_type == "INDIVIDUAL"]
print("== donors: individuals", ind.donor_key.nunique(), "| all keys", n.donor_key.nunique())
for who, key in [("SHEAR, HERBERT", "71bff575bdc1"), ("CAYRE, JOSEPH", "1bc54314d330"), ("COLLIS, STEVEN", "7d37f9b78787"), ("FRIEND, DONALD", "6ab402180935"), ("RUDY, DEBORAH", "e0a9ab832bb7")]:
    g = n[n.donor_key == key]
    print(f"   {who}: rows={len(g)} total=${g.amt.sum():,.0f} cities={sorted(g.contributor_city.unique())}")

# occupations / employers
print("== occupation typo keys left:", int(ind.contributor_occupation.isin(json.load(open(S + '/typo_keys.json')) if os.path.exists(S + '/typo_keys.json') else []).sum()))
vc = ind.contributor_employer.value_counts()
for name in ["BANK OF AMERICA", "MERRILL LYNCH", "NORTHWELL", "SKADDEN", "AMAZON STUDIOS", "VENSURE EMPLOYER SERVICES"]:
    print(f"   employer {name!r}: {int(vc.get(name, 0))} rows")

# addresses / geo
print("== foreign rows (must equal raw):")
from fec.cleaning.foreign_addresses import foreign_address_mask
rawfull = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, usecols=["sub_id", "contributor_street_1", "contributor_street_2", "contributor_city", "contributor_state", "contributor_zip"], low_memory=False).set_index("sub_id")
fm = foreign_address_mask(rawfull.reset_index()).to_numpy()
nn = n.set_index("sub_id")
ok = sum(1 for i in rawfull.index[fm] if all(str(rawfull.at[i, c]).strip() == str(nn.at[i, c]).strip() for c in ["contributor_street_1", "contributor_city", "contributor_state", "contributor_zip"]))
print("   foreign:", int(fm.sum()), "| identical:", ok, "| with coords:", int((nn.loc[rawfull.index[fm], "latitude"] != "").sum()))
print("== contributor coords:", int((ind.latitude != "").sum()), "/", len(ind))
addr = json.load(open("data/resolve_employer_addr.json"))
manual = [k for k, v in addr.items() if str(v.get("method", "")).startswith("manual")]
act = ind[ind.employer_status == "active"]
has = act.contributor_employer.map(lambda x: bool((addr.get(x) or {}).get("employer_address")))
print("== employer cache:", len(addr), "entries | manual:", len(manual), "| active rows with street address:", int(has.sum()), "/", len(act), f"({has.mean()*100:.1f}%)")
e = pd.read_csv("data/employer_locations.csv", dtype=str, keep_default_na=False)
print("== employer_locations:", len(e), "rows | with coords:", int((e.employer_latitude != "").sum()))
mrows = list(csv.DictReader(open("data/manual_employer_addresses.csv", encoding="utf-8", newline="")))
print("== manual_employer_addresses.csv:", len(mrows), "rows | today's:", sum(1 for r in mrows if "2026-09-22" in r["note"]), "| header ok:", list(mrows[0].keys())[0] == "name")
