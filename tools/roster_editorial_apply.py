"""Write verified employer / title / public work address into the editorial-only roster rows.

Input: a JSON list of verification records (one per person) produced by the
`verify-editorial-roster` research workflow, each with `person` {id, files, name}
and `final` {status, employer, occupation, office_kind, office{...},
current_address_assessment, confidence, role_sources, office_sources, ...}.

Rules
- Only rows whose donor_key has NO filing in data/contributions_cleaned.csv are
  touched (FEC-linked rows are owned by sync_rosters.py).
- Employer / occupation are written when the record is not low confidence.
  The employer is matched to the spelling already used in the cleaned data
  (same canonical_key or an employer_name_rules synonym) so one company has one
  name across donors and rosters.
- The address is replaced only by a sourced public work address (office_kind
  other than none_public, confidence not low). Rows with no public work
  address keep their existing address untouched and are listed for review.
  Home addresses are never researched.
- Every change is written to data/_review/roster_editorial_verification.csv
  (old value, new value, sources, confidence) so any row can be reverted.

    python tools/roster_editorial_apply.py results.json [--dry-run]
"""
from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fec.cleaning.employer_synonyms import canonical_key  # noqa: E402
from fec.cleaning.employer_synonyms.synonyms import EMPLOYER_SYNONYMS  # noqa: E402
from fec.database.roster_sync import read_roster, write_roster  # noqa: E402
from fec.env import CLEANED_CSV, PROJECT_ROOT  # noqa: E402
from fec.geocoding import engines  # noqa: E402

ROSTER_DIR = PROJECT_ROOT / "data" / "database"
PREFIX = {"leaders": "leader", "key_accomplices": "accomplice"}
REVIEW = PROJECT_ROOT / "data" / "_review" / "roster_editorial_verification.csv"


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


def geocode(office: dict) -> tuple[str, str]:
    """Census for US offices (Nominatim fallback); Nominatim worldwide for foreign ones."""
    street = " ".join(x for x in [office["street_1"], office["street_2"]] if x)
    try:
        if (office.get("country") or "US") == "US":
            lat, lng, _ = engines.census(office["street_1"], office["city"], office["state"], office["zip"])
            if lat is None:
                time.sleep(engines.NOMINATIM_DELAY)
                lat, lng, *_ = engines.nominatim(office["street_1"], office["city"], office["state"], office["zip"])
        else:
            time.sleep(engines.NOMINATIM_DELAY)
            lat, lng, *_ = engines.nominatim_international(street, office["city"], office.get("country", ""), office["zip"])
    except (engines.CensusUnavailable, engines.NominatimUnavailable):
        lat = None
    if lat is None:
        # PO boxes / mail drops do not geocode to a street: fall back to the ZIP centroid,
        # never to the row's previous coordinates (they belong to the old address)
        from fec.geocoding.pipeline import _zip_centroids
        centroid = _zip_centroids().get(str(office.get("zip", "")).zfill(5)) if (office.get("country") or "US") == "US" else None
        if not centroid:
            return "", ""
        lat, lng = centroid
    return f"{float(lat):.7f}", f"{float(lng):.7f}"


def main(results_path: str, dry_run: bool) -> int:
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
                    row[f"{p}_street_1"] = office["street_1"]
                    row[f"{p}_street_2"] = office.get("street_2", "")
                    row[f"{p}_city"] = office["city"]
                    row[f"{p}_state"] = office.get("state", "")
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
