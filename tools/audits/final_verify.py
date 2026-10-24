"""Final handover verification of data/output/contributions_cleaned.csv and its companions.

Exits 1 when data/reports/quality_gates.json shows a gate that failed or never ran. resolve.py --apply
writes that report on the final data; clean.py's preliminary report (resolve-only gates
not_run because employer_status does not exist yet) or a report for a different row count fails here.
"""
import csv
import hashlib
import json
import os
import subprocess
import sys

import pandas as pd

# run as 'python tools/audits/final_verify.py': the repo root holds the fec package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

S = "tools/audits"
QUALITY_GATES_JSON = "data/reports/quality_gates.json"


def quality_gate_problems(report: dict, rows: int | None = None) -> list[str]:
    """Every reason the gate report does not prove the final file passed; empty when it does."""
    checks = report.get("checks") or {}
    problems = [] if checks else ["the report holds no gate results"]
    for name, check in checks.items():
        if not isinstance(check, dict):
            continue
        if check.get("not_run"):
            problems.append(f"{name}: not run ({check.get('reason') or 'missing columns'})")
        elif check.get("passed") is False:
            problems.append(f"{name}: failed")
        elif check.get("passed") is not True:
            problems.append(f"{name}: no pass/fail result")
    if report.get("passed") is not True:
        problems.append("overall result is not passed")
    if rows is not None and checks.get("row_count") != rows:
        problems.append(f"report covers {checks.get('row_count')} rows, the cleaned file has {rows}")
    return problems


def check_quality_gates(path: str = QUALITY_GATES_JSON, rows: int | None = None) -> list[str]:
    if not os.path.exists(path):
        return [f"{path} is missing"]
    with open(path, encoding="utf-8") as handle:
        report = json.load(handle)
    problems = quality_gate_problems(report, rows)
    print("== quality gates passed:", report.get("passed"), "| stage:", report.get("stage", "?"),
          "| issues:", report.get("issues"))
    for problem in problems:
        print("   GATE PROBLEM:", problem)
    return problems


def held_identity_problems(df: pd.DataFrame) -> list[str]:
    """Check explicit and address-based holds against their required key and status."""
    from fec.donor_match import rules as identity_rules

    required = {"sub_id", "donor_key", "identity_status"}
    if identity_rules.HELD_ADDRESSES:
        required.update({"contributor_name", "contributor_street_1", "contributor_zip"})
    missing_columns = sorted(required - set(df.columns))
    if missing_columns:
        return ["held identity check missing columns: " + ", ".join(missing_columns)]

    problems = []
    present_ids = set(df["sub_id"].astype(str).str.strip())
    missing_ids = set(identity_rules.HELD_FILINGS) - present_ids
    if missing_ids:
        problems.append(f"held filings missing: {len(missing_ids)} explicitly listed sub_id(s)")

    groups = identity_rules.held_groups(df)
    expected = groups.ne("")
    group_keys = {
        group: hashlib.sha256(f"HOLD|{group}".encode()).hexdigest()[:12]
        for group in set(identity_rules.HELD_FILINGS.values()) | set(identity_rules.HELD_ADDRESSES.values())
    }
    expected_keys = groups[expected].map(group_keys)
    keys = df["donor_key"].fillna("").astype(str)
    statuses = df["identity_status"].fillna("")
    wrong_keys = int(keys[expected].ne(expected_keys).sum())
    wrong_statuses = int(statuses[expected].ne("held").sum())
    if wrong_keys:
        problems.append(f"held filings on the wrong donor key: {wrong_keys} row(s)")
    if wrong_statuses:
        problems.append(f"held filings without held status: {wrong_statuses} row(s)")

    # Include observed keys too: a misassigned held row must not share a person's key.
    held_keys = set(group_keys.values()) | set(keys[expected])
    held_keys.discard("")
    mixed = int((~expected & keys.isin(held_keys)).sum())
    falsely_held = int((~expected & statuses.eq("held")).sum())
    if mixed:
        problems.append(f"held filings not kept apart: {mixed} unheld row(s) share their keys")
    if falsely_held:
        problems.append(f"held status without a matching hold rule: {falsely_held} row(s)")
    return problems


def main() -> int:
    n = pd.read_csv("data/output/contributions_cleaned.csv", dtype=str, keep_default_na=False, low_memory=False)
    n["amt"] = pd.to_numeric(n.contribution_receipt_amount, errors="coerce").fillna(0)
    raw = pd.read_csv("data/raw/contributions.csv", dtype=str, keep_default_na=False, usecols=["sub_id", "committee_id", "contribution_receipt_date"], low_memory=False)
    problems = []  # every check that must hold; any entry makes the exit code 1
    same_ids, dup_ids = set(n.sub_id) == set(raw.sub_id), int(n.sub_id.duplicated().sum())
    print("== rows: cleaned", len(n), "| raw", len(raw), "| same sub_ids:", same_ids, "| dup sub_id:", dup_ids)
    if len(n) != len(raw) or not same_ids:
        problems.append(f"cleaned rows {len(n)} / raw rows {len(raw)}, same sub_ids: {same_ids}")
    if dup_ids:
        problems.append(f"{dup_ids} duplicate sub_id(s)")
    d = pd.to_datetime(raw.contribution_receipt_date, errors="coerce")
    print("== latest date per committee:", raw.assign(d=d).groupby("committee_id").d.max().dt.date.to_dict())

    gate_problems = check_quality_gates(QUALITY_GATES_JSON, rows=len(n))
    a = json.load(open("data/reports/audit_summary.json"))
    print("== audit: changes", a["changes"], "| untracked", a["untracked_changes"])
    for step in ["enh_normalize_occupation_style", "enh_occupation_typo_fixes", "enh_employer_synonyms_final", "foreign_address_restore",
                 "safety_disambiguate_vague_occupation", "streets_trim_truncated"]:
        print(f"   {step}: {a['steps'].get(step, {}).get('changes')}")
    summed = a["changes"] == sum(v["changes"] for v in a["steps"].values())
    print("   summary total == sum of steps:", summed)
    if a["untracked_changes"]:
        problems.append(f"{a['untracked_changes']} change(s) outside the audited steps")
    if not summed:
        problems.append("audit summary total differs from the sum of its steps")
    for occ in ["DEVELOPER", "REAL ESTATE DEVELOPER", "SOFTWARE DEVELOPER"]:
        print(f"   {occ}: {int((n.contributor_occupation == occ).sum())} rows")
    smith = n[n.donor_key == "34befe88f597"]
    print("   SMITH, ARTHUR occupations:", smith.contributor_occupation.value_counts().to_dict())
    neub = n[n.contributor_name.str.startswith("NEUBERGER, YEHUDA")].sort_values("contribution_receipt_date").iloc[-1]
    print("   NEUBERGER latest:", neub.contributor_street_1, "|", neub.contributor_street_2, "|", neub.latitude, neub.longitude)
    # Display-only company names do not create previous work history.
    from fec.cleaning.rules.manual import _read_overrides
    display_previous_ids = set()
    overrides = _read_overrides(False, display_previous_ids=display_previous_ids)
    nn_ = n.set_index("sub_id")
    # Compare company overrides using the same documented names as cleaning.
    from fec.cleaning.work.employer_aliases import reviewed_employer_name, scoped_employer_rules
    scoped = scoped_employer_rules(n)
    for sid, fields in overrides.items():
        if sid not in nn_.index or nn_.at[sid, "entity_type"] != "INDIVIDUAL":
            continue
        row = nn_.loc[sid]
        for column in ("contributor_employer", "previous_employer"):
            if column in fields and fields[column] is not pd.NA:
                fields[column] = reviewed_employer_name(
                    fields[column], row["donor_key"], row["two_year_transaction_period"],
                    scoped if row["identity_status"] == "confirmed" else None,
                )
    overridden = [
        (sid, col) for sid, fields in overrides.items() if sid in nn_.index
        for col, val in fields.items() if col in nn_.columns
        and not (col == "previous_employer" and sid in display_previous_ids
                 and not nn_.at[sid, col].strip())
        and nn_.at[sid, col].strip().upper() != ("" if val is pd.NA else str(val)).strip().upper()
    ]
    print("== manual overrides not in the final file:", len(overridden), overridden[:5])
    if overridden:
        problems.append(f"{len(overridden)} manual override value(s) changed by a later step: {overridden[:5]}")
    # a previous employer belongs to a retired filing only, and never to a placeholder
    from fec.cleaning.work.status import is_real_employer
    prev = n.previous_employer.str.strip()
    not_retired = int((prev.ne("") & n.employer_status.ne("retired")).sum())
    placeholder = sorted({v for v in prev.unique() if v and v != "SELF-EMPLOYED" and not is_real_employer(v)})
    print("== previous employer on a non-retired filing:", not_retired, "| non-company values:", placeholder[:5])
    if not_retired:
        problems.append(f"{not_retired} non-retired filing(s) carry a previous employer")
    if placeholder:
        problems.append(f"previous employer values that are not companies: {placeholder[:5]}")
    # every reviewed identity case still holds on this file
    cases = subprocess.run([sys.executable, S + "/identity_cases.py", "data/output/contributions_cleaned.csv"],
                           capture_output=True, text=True, encoding="utf-8",
                           env={**os.environ, "PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8"})
    print("== identity cases exit:", cases.returncode, "|", (cases.stdout.strip().splitlines() or ["?"])[-1].strip())
    if cases.returncode:
        failed = [line for line in cases.stdout.splitlines() if line.startswith("FAIL")]
        problems.append(f"identity_cases.py: {len(failed)} case(s) failed: " + "; ".join(line[6:].strip() for line in failed[:5]))

    # donor identity rules all applied
    rules = list(csv.DictReader(open("data/rules/donor_identity_rules.csv", encoding="utf-8-sig", newline="")))
    merges = [r for r in rules if r["action"].lower() == "merge_keys"]
    dropped_present, keep_missing = [], []
    for rule in merges:
        sub_ids = (rule.get("sub_ids") or "").split()
        selected = n[n.sub_id.isin(sub_ids)] if sub_ids else n
        if selected.empty:
            continue
        keys = set(selected.donor_key)
        if rule["donor_key_b"] in keys:
            dropped_present.append(rule["donor_key_b"])
        if rule["donor_key_a"] not in keys:
            keep_missing.append(rule["donor_key_a"])
        if sub_ids and selected.donor_key.ne(rule["donor_key_a"]).any():
            problems.append(f"reviewed committee filings on a different key: {rule['group']}")
        if rule.get("canonical_name"):
            named = selected if sub_ids else selected[selected.donor_key.eq(rule["donor_key_a"])]
            if named.contributor_name.ne(rule["canonical_name"]).any():
                problems.append(f"committee name not applied: {rule['canonical_name']}")
    print("== identity rules:", len(rules), "| merges:", len(merges), "| dropped keys still present:", len(dropped_present), "| keep keys absent:", len(keep_missing))
    if dropped_present or keep_missing:
        problems.append(f"identity merges not applied: {len(dropped_present)} dropped key(s) present, {len(keep_missing)} kept key(s) absent")
    # held filings stay together and away from every person's filings
    hold_problems = held_identity_problems(n)
    print("== held filings:", int(n.identity_status.eq("held").sum()), "| hold rule issues:", len(hold_problems))
    problems.extend(hold_problems)
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
    from fec.cleaning.addresses.foreign import foreign_address_mask
    rawfull = pd.read_csv("data/raw/contributions.csv", dtype=str, keep_default_na=False, usecols=["sub_id", "contributor_street_1", "contributor_street_2", "contributor_city", "contributor_state", "contributor_zip"], low_memory=False).set_index("sub_id")
    fm = foreign_address_mask(rawfull.reset_index()).to_numpy()
    nn = n.set_index("sub_id")
    ok = sum(1 for i in rawfull.index[fm] if all(str(rawfull.at[i, c]).strip() == str(nn.at[i, c]).strip() for c in ["contributor_street_1", "contributor_city", "contributor_state", "contributor_zip"]))
    foreign_coords = int((nn.loc[rawfull.index[fm], "latitude"] != "").sum())
    print("   foreign:", int(fm.sum()), "| identical:", ok, "| with coords:", foreign_coords)
    if ok != int(fm.sum()) or foreign_coords:
        problems.append(f"foreign rows: {int(fm.sum()) - ok} changed from the raw filing, {foreign_coords} geocoded")
    print("== contributor coords:", int((ind.latitude != "").sum()), "/", len(ind))
    addr = json.load(open("data/cache/resolve_employer_addr.json"))
    manual = [k for k, v in addr.items() if str(v.get("method", "")).startswith("manual")]
    act = ind[ind.employer_status == "active"]
    has = act.contributor_employer.map(lambda x: bool((addr.get(x) or {}).get("employer_address")))
    print("== employer cache:", len(addr), "entries | manual:", len(manual), "| active rows with street address:", int(has.sum()), "/", len(act), f"({has.mean()*100:.1f}%)")
    e = pd.read_csv("data/output/employer_locations.csv", dtype=str, keep_default_na=False)
    print("== employer_locations:", len(e), "rows | with coords:", int((e.employer_latitude != "").sum()))
    mrows = list(csv.DictReader(open("data/rules/manual_employer_addresses.csv", encoding="utf-8", newline="")))
    print("== manual_employer_addresses.csv:", len(mrows), "rows | header ok:", list(mrows[0].keys())[0] == "name")

    problems += [f"quality gates ({QUALITY_GATES_JSON}, rerun from clean.py through resolve.py --apply): {p}" for p in gate_problems]
    if problems:
        print(f"\nFAIL: {len(problems)} problem(s):")
        for problem in problems:
            print("   -", problem)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
