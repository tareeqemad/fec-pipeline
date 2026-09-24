"""Remove cache entries the current data no longer uses, proving the pipeline result is unchanged.

  python prune_caches.py dry   [repo] [out_dir] -> writes pruned copies + report to <out_dir>/pruned/, touches nothing in data/
  python prune_caches.py apply [repo] [out_dir] -> same checks, then backs up data/ caches to <out_dir>/backup/ and writes the pruned files

"Used" is decided by the pipeline's own functions:
  geocode_cache.json         keys built by geocode_addresses / geocode_employer_addresses / build_employers for the
                             current data, plus keys referenced in code (reviewed points)
  resolve_employer_addr.json keys whose exact name or canonical key matches any employer or previous employer in the
                             data or in manual_employer_addresses.csv (the lookup groups spellings by canonical key)
  resolve_prev_employer.json donor:<key> entries for donor keys present in the data
  fec_address_cache.json     'NAME|STATE' entries for names present in the raw filings
Verification: apply_results (resolve) and the employer address candidates are identical with the full and the
pruned caches, and no geocode key used by the data would need a new (paid) lookup.
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2] if len(sys.argv) < 3 else Path(sys.argv[2])
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else REPO.parent / "prune_caches_out"
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "."))

import geocode as geocode_script  # noqa: E402  (only helper functions are used; main() is not called)
from fec.cleaning.employer_synonyms import canonical_key  # noqa: E402  (the same one resolve's lookup uses)
from fec.geocoding import pipeline as gp  # noqa: E402
from fec.geocoding import reviewed_points  # noqa: E402
from fec.io import read_pipeline_csv  # noqa: E402
from fec.resolve.pipeline.apply import apply_results  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "dry"
DATA = REPO / "data"
kw = dict(dtype=str, keep_default_na=False, na_values=[])


def load(name):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


class DictCache(dict):
    """Read-only stand-in for fec.resolve Cache (get / __contains__ / items)."""

    def put(self, *a, **k):
        raise RuntimeError("cache write attempted during verification")


geo = load("geocode_cache.json")
addr = load("resolve_employer_addr.json")
prev = load("resolve_prev_employer.json")
fecaddr = load("fec_address_cache.json")

df = read_pipeline_csv(str(DATA / "contributions_cleaned.csv"))
raw = pd.read_csv(DATA / "contributions.csv", usecols=["contributor_name", "contributor_state"], **kw)
emp_loc = pd.read_csv(DATA / "employer_locations.csv", **kw)
manual = pd.read_csv(DATA / "manual_employer_addresses.csv", **kw)

# ---------- resolve_prev_employer.json ----------
donor_keys = set(df["donor_key"].dropna().astype(str))
prev_keep = {k: v for k, v in prev.items() if k.split(":", 1)[1] in donor_keys}

# ---------- resolve_employer_addr.json ----------
names = set(df["contributor_employer"].dropna().astype(str)) | set(df["previous_employer"].dropna().astype(str))
names |= set(emp_loc["employer_name"]) | set(manual.iloc[:, 0])
# retired rows look the address up by the previous-employer cache's own names (apply._previous_employer_identity)
for entry in prev_keep.values():
    names |= {str(entry.get(f) or "") for f in ("employer", "employer_normalized", "employer_source")}
names = {n.strip().upper() for n in names if n and n.strip()}
canon = {canonical_key(n) for n in names} - {""}
addr_keep = {k: v for k, v in addr.items()
             if str(k).strip().upper() in names or canonical_key(str(k)) in canon}

# ---------- verification of resolve with the pruned caches ----------
full = apply_results(df.copy(), DictCache(prev), DictCache(addr))
pruned = apply_results(df.copy(), DictCache(prev_keep), DictCache(addr_keep))
cols = ["employer_address", "employer_city", "employer_state", "employer_zip", "resolve_method",
        "resolve_confidence", "employer_status", "previous_employer"]
diff = {c: int((full[c].fillna("").astype(str) != pruned[c].fillna("").astype(str)).sum()) for c in cols}
print("resolve apply_results differences (must all be 0):", diff)

# employer location candidates (what geocode --employer-only geocodes and build_employers publishes)
def candidates(entries, frame):
    tmp = OUT / "tmp_addr"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "resolve_employer_addr.json").write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    rows = geocode_script._all_employer_addresses(frame, str(tmp))
    mask = rows["employer_address"].notna() & (rows["employer_address"] != "")
    return rows.loc[mask], set(gp._employer_keys(rows.loc[mask]))

cand_full, ekeys_full = candidates(addr, full)
cand_pruned, ekeys_pruned = candidates(addr_keep, pruned)
print("employer geocode keys full/pruned:", len(ekeys_full), len(ekeys_pruned), "| identical:", ekeys_full == ekeys_pruned)

# ---------- geocode_cache.json ----------
ckeys = gp._contributor_keys(df)
ckeys = set(ckeys[~gp.foreign_address_mask(df)])
loc_keys = set(gp._employer_keys(emp_loc))
code_keys = set()
for name in dir(reviewed_points):
    value = getattr(reviewed_points, name)
    if isinstance(value, (set, frozenset, list, tuple, dict)):
        code_keys |= {k for k in value if isinstance(k, str) and k.count("|") == 3}
used = ckeys | ekeys_full | loc_keys | code_keys
geo_keep = {k: v for k, v in geo.items() if k in used}


from fec.geocoding.cache import GeoCache  # noqa: E402


def todo(data, keys):
    view = GeoCache.__new__(GeoCache)  # the real class, in memory only (no path, never saved)
    view.path, view.data = None, dict(data)
    return {k for k in keys if gp._needs_lookup(k, view)}

need_full = todo(geo, ckeys | ekeys_full)
need_pruned = todo(geo_keep, ckeys | ekeys_full)
print("geocode keys needing a lookup full/pruned (must be equal):", len(need_full), len(need_pruned), "| extra:", len(need_pruned - need_full))

# ---------- fec_address_cache.json ----------
present = set(raw["contributor_name"].str.strip().str.upper() + "|" + raw["contributor_state"].str.strip().str.upper())
present |= set(df["contributor_name"].str.strip().str.upper() + "|" + df["contributor_state"].str.strip().str.upper())
fec_keep = {k: v for k, v in fecaddr.items() if k.strip().upper() in present}

report = {
    "geocode_cache.json": (len(geo), len(geo_keep)),
    "resolve_employer_addr.json": (len(addr), len(addr_keep)),
    "resolve_prev_employer.json": (len(prev), len(prev_keep)),
    "fec_address_cache.json": (len(fecaddr), len(fec_keep)),
}
for name, (before, after) in report.items():
    print(f"{name}: {before} -> {after}  (remove {before - after})")

# ---------- build_employers with the pruned geocode cache ----------
import build_employers  # noqa: E402


def built(geo_data):
    tmp = OUT / "tmp_build"
    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("resolve_employer_addr.json", "manual_employer_addresses.csv", "employer_locations.csv"):
        shutil.copy(DATA / name, tmp / name)
    (tmp / "geocode_cache.json").write_text(json.dumps(geo_data, ensure_ascii=False), encoding="utf-8")
    saved = build_employers.DATA_DIR, build_employers.EMPLOYER_LOCATIONS_CSV
    build_employers.DATA_DIR, build_employers.EMPLOYER_LOCATIONS_CSV = tmp, tmp / "employer_locations.csv"
    try:
        locations, _review = build_employers.build_locations(df.copy())
    finally:
        build_employers.DATA_DIR, build_employers.EMPLOYER_LOCATIONS_CSV = saved
    return locations


# every key build_employers looks an office up under is used (the office's raw,
# normalised, donor-form and bare-street spellings)
looked_up = set()
_original_geocodes = build_employers._geocodes


class _Recording(dict):
    def get(self, key, default=None):
        looked_up.add(tuple(key))
        return super().get(key, default)


build_employers._geocodes = lambda: _Recording(_original_geocodes())
built_full = built(geo)
build_employers._geocodes = _original_geocodes
extra = {k: v for k, v in geo.items()
         if k not in geo_keep and tuple(part.strip().upper() for part in k.split("|")) in looked_up}
print("geocode keys kept because build_employers looks them up:", len(extra))
geo_keep.update(extra)
built_pruned = built(geo_keep)
norm = lambda frame: frame.astype(object).where(frame.notna(), "").astype(str).reset_index(drop=True)
same_build = norm(built_full).equals(norm(built_pruned))
print("build_employers output identical with the pruned geocode cache:", same_build)

report["geocode_cache.json"] = (len(geo), len(geo_keep))
print("geocode_cache.json final:", len(geo), "->", len(geo_keep))
ok = all(v == 0 for v in diff.values()) and ekeys_full == ekeys_pruned and need_pruned == need_full and same_build
print("VERIFIED:", ok)

target = OUT / "pruned"
target.mkdir(parents=True, exist_ok=True)
outputs = {"geocode_cache.json": geo_keep, "resolve_employer_addr.json": addr_keep,
           "resolve_prev_employer.json": prev_keep, "fec_address_cache.json": fec_keep}
for name, data in outputs.items():
    # same format as each cache's own writer: GeoCache/Cache use json.dump defaults, ensure_ascii=False
    original = (DATA / name).read_text(encoding="utf-8")
    indent = 2 if original.startswith("{\n") else None
    (target / name).write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")

if mode == "apply":
    if not ok:
        sys.exit("verification failed: nothing written to data/")
    backup = OUT / "backup"
    backup.mkdir(parents=True, exist_ok=True)
    for name in outputs:
        shutil.copy(DATA / name, backup / name)
        shutil.copy(target / name, DATA / name)
    print("applied; backups in", backup)

if not same_build:
    a, b = norm(built_full), norm(built_pruned)
    print("shapes", a.shape, b.shape)
    if a.shape == b.shape:
        rows = (a != b).any(axis=1)
        print("rows differing:", int(rows.sum()))
        print(pd.concat([a[rows].head(8), b[rows].head(8)]).to_string())
