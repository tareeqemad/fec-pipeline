"""Self-employed audit on the final cleaned data (run after the whole chain: resolve adds employer_status)."""
import re
import pandas as pd

cols = ["sub_id", "donor_key", "entity_type", "contributor_name", "contributor_employer", "contributor_occupation",
        "occupation_category", "employer_status", "previous_employer"]
n = pd.read_csv("data/contributions_cleaned.csv", dtype=str, keep_default_na=False, low_memory=False, usecols=cols)
r = pd.read_csv("data/contributions.csv", dtype=str, keep_default_na=False, low_memory=False,
                usecols=["sub_id", "contributor_employer", "contributor_occupation"]).set_index("sub_id")
ind = n[n.entity_type == "INDIVIDUAL"].copy()
ind["raw_emp"] = ind.sub_id.map(r.contributor_employer).str.upper().str.strip()
ind["raw_occ"] = ind.sub_id.map(r.contributor_occupation).str.upper().str.strip()
SE = re.compile(r"^\s*(SELF[\s\-]*EMPLOYED?|SELF|SELF[\s\-]*EMP\.?|SELFEMPLOYED|SELF EMPLYED|SELF-EMPLOYEED|SELF EMPLOYEE|SELF-EMPLOY|SELF EMPLYOED|SELF-EMPLOYED PERSON|OWNER)\s*$")
raw_se_emp = ind.raw_emp.str.match(SE)
raw_se_occ = ind.raw_occ.str.match(SE)
pd.set_option("display.width", 230); pd.set_option("display.max_colwidth", 70)

print("== status distribution:", ind.employer_status.value_counts().to_dict())
print("== raw employer = self-employed variant:", int(raw_se_emp.sum()), "| raw occupation = self-employed variant:", int(raw_se_occ.sum()))
a = ind[raw_se_emp]
print("   cleaned employer:", a.contributor_employer.value_counts().head(6).to_dict())
print("   cleaned status:", a.employer_status.value_counts().to_dict())
keep = (a.raw_occ != "") & ~a.raw_occ.str.match(SE)
print("   real raw occupation kept:", int((a[keep].contributor_occupation.str.upper() == a[keep].raw_occ).sum()), "/", int(keep.sum()))
print("   real raw occupation replaced by a status word:")
print(a[keep & a.contributor_occupation.str.upper().isin(["SELF-EMPLOYED", "NOT EMPLOYED", "RETIRED", ""])][["raw_occ", "contributor_occupation"]].value_counts().head(10).to_string())

b = ind[(ind.contributor_employer == "SELF-EMPLOYED") & ~raw_se_emp & (ind.raw_emp != "")]
print("\n== cleaned SELF-EMPLOYED while the raw employer was something else:", len(b), "rows,", b.raw_emp.nunique(), "distinct raw employers")
print(b.groupby(["raw_emp", "raw_occ", "contributor_occupation"]).size().sort_values(ascending=False).head(30).to_string())

c = ind[raw_se_occ & ~raw_se_emp & (ind.raw_emp != "")]
print("\n== raw occupation = self-employed but raw employer a real name:", len(c), "| cleaned status:", c.employer_status.value_counts().to_dict())
print(c.groupby(["raw_emp", "contributor_employer", "contributor_occupation", "employer_status"]).size().sort_values(ascending=False).head(15).to_string())

g = ind.groupby("donor_key").agg(se=("employer_status", lambda s: (s == "self_employed").any()),
                                 act=("employer_status", lambda s: (s == "active").any()))
mixed = g[g.se & g.act]
print("\n== donors with BOTH self_employed rows and active-company rows:", len(mixed), "of", len(g))
ex = ind[ind.donor_key.isin(mixed.sample(min(12, len(mixed)), random_state=4).index)]
print(ex.groupby(["contributor_name", "contributor_employer", "contributor_occupation", "employer_status"]).size().to_string())

s = ind[ind.employer_status == "self_employed"]
print("\n== self_employed rows:", len(s), "| occupation blank:", int((s.contributor_occupation == "").sum()),
      "| occupation == SELF-EMPLOYED:", int((s.contributor_occupation == "SELF-EMPLOYED").sum()),
      "| category:", s.occupation_category.value_counts().head(5).to_dict())
print("   previous_employer filled on self_employed rows:", int((s.previous_employer != "").sum()))
own = ind[(ind.employer_status == "self_employed") & (ind.raw_emp != "") & ~raw_se_emp]
print("\n== self_employed rows whose raw employer was a name (own name / own firm):", len(own))
print(own.groupby(["contributor_name", "raw_emp", "contributor_employer", "contributor_occupation"]).size().head(20).to_string())
