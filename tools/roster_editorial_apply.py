"""Manually apply sourced jobs and public work addresses to unlinked roster rows.

Input is a JSON list of research records with `person` and `final` fields.
Low-confidence records do not change jobs or addresses. US streets use the
cleaning rules; foreign streets stay as sourced. Changes and sources are logged
to data/reports/review/roster_editorial_verification.csv.

This tool is not part of the normal pipeline.

    python tools/roster_editorial_apply.py results.json [--dry-run]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fec.cleaning.addresses.streets import clean_streets  # noqa: E402
from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import EMPLOYER_SYNONYMS  # noqa: E402
from fec.env import CLEANED_CSV, PROJECT_ROOT, load_env  # noqa: E402
from fec.geocoding.pipeline import _geocode_one
from fec.geocoding.providers import CensusUnavailable, NominatimUnavailable
from fec.io import replace_file  # noqa: E402

ROSTER_DIR = PROJECT_ROOT / "data" / "rules"
PREFIX = {"leaders": "leader", "key_accomplices": "accomplice"}
REVIEW = PROJECT_ROOT / "data" / "reports" / "review" / "roster_editorial_verification.csv"


def read_roster(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


def write_roster(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    """Replace the CSV atomically, preserving its line endings."""
    terminator = "\r\n" if path.exists() and b"\r\n" in path.read_bytes() else "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator=terminator)
        writer.writeheader()
        writer.writerows(rows)
    replace_file(str(temp), str(path))


def _employer_vocabulary() -> dict[str, str]:
    """canonical_key -> the most-used spelling of that employer in the cleaned data."""
    df = pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, low_memory=False,
                     usecols=["entity_type", "contributor_employer"])
    counts = df.loc[df.entity_type == "INDIVIDUAL", "contributor_employer"].value_counts()
    vocab: dict[str, str] = {}
    for name in counts.index:
        if name:
            vocab.setdefault(canonical_key(name), name)
    return vocab


def canonical_employer(name: str, vocab: dict[str, str]) -> str:
    name = (name or "").strip().upper()
    if not name:
        return ""
    name = EMPLOYER_SYNONYMS.get(name, name)
    return vocab.get(canonical_key(name), name)


def office_streets(office: dict) -> tuple[str, str]:
    """Format US office streets; keep foreign streets as sourced."""
    street_1, street_2 = office["street_1"], office.get("street_2", "") or ""
    if (office.get("country") or "US") != "US":
        return street_1, street_2
    frame = pd.DataFrame([{
        "contributor_street_1": (street_1 or "").strip() or None,
        "contributor_street_2": street_2.strip() or None,
        "contributor_first_name": "",
        "contributor_last_name": "",
    }])
    frame, _ = clean_streets(frame)
    return tuple("" if pd.isna(frame.at[0, column]) else str(frame.at[0, column])
                 for column in ("contributor_street_1", "contributor_street_2"))


def geocode(office: dict) -> tuple[str, str]:
    """Use the shared provider order; a missing address result stays blank."""
    country = office.get("country") or "US"
    street, state = office["street_1"], office["state"]
    if country not in {"US", "USA"}:
        street = " ".join(part for part in (street, office.get("street_2")) if part)
        state = country
    try:
        lat, lng, _country, _source = _geocode_one(
            street, office["city"], state, office["zip"],
        )
    except (CensusUnavailable, NominatimUnavailable):
        return "", ""
    if lat is None:
        return "", ""
    return f"{float(lat):.7f}", f"{float(lng):.7f}"


def main(results_path: str, dry_run: bool) -> int:
    load_env()
    records = json.loads(Path(results_path).read_text(encoding="utf-8"))
    linked = set(pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, usecols=["donor_key"]).donor_key)
    vocab = _employer_vocabulary()
    rosters = {f: read_roster(ROSTER_DIR / f"{f}.csv") for f in PREFIX}
    review = []

    for rec in records:
        person, final = rec.get("person") or {}, rec.get("final")
        if not final:
            review.append({"name": person.get("name", ""), "file": ",".join(person.get("files", [])), "action": "no_result"})
            continue
        low = final.get("confidence") == "low"
        status = final.get("status", "unknown")
        employer = canonical_employer(final.get("employer", ""), vocab)
        occupation = (final.get("occupation") or "").strip().upper()
        if status == "retired" and not employer:
            employer, occupation = "RETIRED", occupation or "RETIRED"
        office = final.get("office") or {}
        use_office = (not low and status != "deceased" and final.get("office_kind") != "none_public"
                      and office.get("street_1") and office.get("city"))
        lat = lng = ""
        if use_office and not dry_run:
            lat, lng = geocode(office)

        for fname in person.get("files", []):
            rows, _ = rosters[fname]
            p = PREFIX[fname]
            for row in rows:
                if row["donor_key"] != person["id"] or row["donor_key"] in linked:
                    continue
                before = {k: row.get(k, "") for k in row}
                # a deceased person keeps their last known role (historical record); status is in the review file
                if not low:
                    row[f"{p}_employer"] = employer or row.get(f"{p}_employer", "")
                    row[f"{p}_occupation"] = occupation or row.get(f"{p}_occupation", "")
                if use_office and status != "deceased":
                    row[f"{p}_street_1"], row[f"{p}_street_2"] = office_streets(office)
                    row[f"{p}_city"] = office["city"].strip().upper()
                    row[f"{p}_state"] = (office.get("state") or "").strip().upper()
                    row[f"{p}_zip"] = office.get("zip", "")
                    if f"{p}_country" in row:
                        country = office.get("country") or "US"
                        row[f"{p}_country"] = "" if country == "US" else country
                    row["address_lat"], row["address_lng"] = lat, lng
                changed = {k: (before[k], row[k]) for k in row if before[k] != row[k]}
                review.append({
                    "name": person["name"], "file": fname, "confidence": final.get("confidence", ""),
                    "status": status,
                    "action": ("address+work" if use_office else "work_only") if changed else "unchanged",
                    "previous_address_assessment": final.get("current_address_assessment", ""),
                    "office_kind": final.get("office_kind", ""),
                    "old_employer": before.get(f"{p}_employer", ""), "new_employer": row.get(f"{p}_employer", ""),
                    "old_occupation": before.get(f"{p}_occupation", ""), "new_occupation": row.get(f"{p}_occupation", ""),
                    "old_address": ", ".join(x for x in [before.get(f"{p}_street_1"), before.get(f"{p}_street_2"), before.get(f"{p}_city"), before.get(f"{p}_state"), before.get(f"{p}_zip")] if x),
                    "new_address": ", ".join(x for x in [row.get(f"{p}_street_1"), row.get(f"{p}_street_2"), row.get(f"{p}_city"), row.get(f"{p}_state"), row.get(f"{p}_zip")] if x),
                    "old_lat_lng": f"{before.get('address_lat', '')},{before.get('address_lng', '')}",
                    "new_lat_lng": f"{row.get('address_lat', '')},{row.get('address_lng', '')}",
                    "role_sources": " | ".join(s.get("url", "") for s in final.get("role_sources", [])),
                    "office_sources": " | ".join(s.get("url", "") for s in final.get("office_sources", [])),
                    "notes": (final.get("decision_notes", "") + " " + final.get("notes", "")).strip(),
                })

    if not dry_run:
        for fname, (rows, fieldnames) in rosters.items():
            write_roster(ROSTER_DIR / f"{fname}.csv", rows, fieldnames)
    REVIEW.parent.mkdir(parents=True, exist_ok=True)
    cols = sorted({k for r in review for k in r}, key=lambda k: list(review[0].keys()).index(k) if k in review[0] else 99)
    with REVIEW.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=cols)
        w.writeheader()
        w.writerows(review)
    df = pd.DataFrame(review)
    print(df.action.value_counts().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--dry-run" in sys.argv[2:]))
