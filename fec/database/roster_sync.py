"""Sync editorial rosters to cleaned FEC data."""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.cleaning.addresses.streets import clean_streets
from fec.donor_match.rules import resolve_donor_key
from fec.env import CLEANED_CSV, RULES_DIR
from fec.io import replace_file

ROSTER_DIR = RULES_DIR
ROSTERS = {"leaders.csv": "leader", "key_accomplices.csv": "accomplice"}

# roster column suffix to cleaned CSV column
SYNCED_FIELDS = {
    "street_1": "contributor_street_1",
    "street_2": "contributor_street_2",
    "city": "contributor_city",
    "state": "contributor_state",
    "zip": "contributor_zip",
    "employer": "contributor_employer",
    "occupation": "contributor_occupation",
}
COORDINATE_FIELDS = {"address_lat": "latitude", "address_lng": "longitude"}
_CLEANED_COLUMNS = ["sub_id", "donor_key", "contribution_receipt_date",
                    *SYNCED_FIELDS.values(), *COORDINATE_FIELDS.values()]


@dataclass
class SyncResult:
    filename: str
    rows: list[dict]
    fieldnames: list[str]
    changes: list[tuple[str, str, str, str]] = field(default_factory=list)   # (name, column, old, new)
    unlinked: list[str] = field(default_factory=list)                        # names with no FEC filing
    broken: list[tuple[str, str]] = field(default_factory=list)              # (name, key) must link, cannot


# newest filing per donor_key: date, then sub_id
def latest_filings(cleaned: pd.DataFrame) -> pd.DataFrame:
    df = cleaned[_CLEANED_COLUMNS].copy()
    df["_date"] = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    df = df.sort_values(["donor_key", "_date", "sub_id"]).drop_duplicates("donor_key", keep="last")
    return df.set_index("donor_key")


# one filing cell as a roster string
def _fec_value(filing: pd.Series, column: str) -> str:
    value = filing[column]
    if pd.isna(value):
        return ""
    value = str(value).strip()
    if column == "contributor_zip":
        value = value[:5]
    elif column in COORDINATE_FIELDS.values() and value:
        value = _coordinate(value)
    return value


# round coordinate to 7 decimals, drop noise
def _coordinate(value: str) -> str:
    try:
        return f"{float(value):.7f}".rstrip("0").rstrip(".")
    except ValueError:
        return value


# a value as text, '' for NaN
def _cell(value) -> str:
    return "" if pd.isna(value) else str(value)


# run a street pair through clean_streets
def pipeline_streets(street_1: str, street_2: str, first: str = "", last: str = "") -> tuple[str, str]:
    frame = pd.DataFrame([{
        "contributor_street_1": (street_1 or "").strip() or None,
        "contributor_street_2": (street_2 or "").strip() or None,
        "contributor_first_name": (first or "").strip().upper(),
        "contributor_last_name": (last or "").strip().upper(),
    }])
    frame, _ = clean_streets(frame)
    return _cell(frame.at[0, "contributor_street_1"]), _cell(frame.at[0, "contributor_street_2"])


# first/last from columns, else 'LAST, FIRST' name
def _person_names(row: dict, prefix: str) -> tuple[str, str]:
    first = (row.get(f"{prefix}_first_name") or "").strip()
    last = (row.get(f"{prefix}_last_name") or "").strip()
    name = (row.get(f"{prefix}_name") or "").strip()
    if (not first or not last) and "," in name:
        last_part, _, first_part = name.partition(",")
        first, last = first or first_part.strip(), last or last_part.strip()
    return first, last


# true if a roster address is foreign
def is_foreign_row(row: dict, prefix: str) -> bool:
    country = (row.get(f"{prefix}_country") or "").strip().upper()
    if country and country not in {"US", "USA"}:
        return True
    return bool(foreign_address_mask(pd.DataFrame([{
        "contributor_street_1": row.get(f"{prefix}_street_1") or "",
        "contributor_street_2": row.get(f"{prefix}_street_2") or "",
        "contributor_city": row.get(f"{prefix}_city") or "",
        "contributor_state": row.get(f"{prefix}_state") or "",
    }])).iloc[0])


# street cells clean_streets would rewrite
def editorial_street_changes(row: dict, prefix: str) -> dict[str, str]:
    if is_foreign_row(row, prefix):
        return {}
    columns = (f"{prefix}_street_1", f"{prefix}_street_2")
    old = tuple((row.get(column) or "").strip() for column in columns)
    new = pipeline_streets(*old, *_person_names(row, prefix))
    return {column: value for column, before, value in zip(columns, old, new) if before != value}


# add occupation column after employer if missing
def _ensure_columns(rows: list[dict], fieldnames: list[str], prefix: str) -> list[str]:
    occupation = f"{prefix}_occupation"
    if occupation not in fieldnames:
        fieldnames.insert(fieldnames.index(f"{prefix}_employer") + 1, occupation)
        for row in rows:
            row[occupation] = ""
    return fieldnames


# sync linked rows; clean editorial-only streets
def sync_rows(rows: list[dict], fieldnames: list[str], prefix: str,
              latest: pd.DataFrame, filename: str = "") -> SyncResult:
    result = SyncResult(filename, rows, _ensure_columns(rows, list(fieldnames), prefix))
    for row in rows:
        name = (row.get(f"{prefix}_name") or "").strip()
        key = resolve_donor_key((row.get("donor_key") or "").strip())
        if key not in latest.index:
            if (row.get("create_if_missing") or "").strip().lower() == "false":
                # loader would fail: donor_key names no donor
                result.broken.append((name, key))
            result.unlinked.append(name)
            for column, new in editorial_street_changes(row, prefix).items():
                result.changes.append((name, column, (row.get(column) or "").strip(), new))
                row[column] = new
            continue
        filing = latest.loc[key]
        targets = {f"{prefix}_{suffix}": col for suffix, col in SYNCED_FIELDS.items()}
        targets.update(COORDINATE_FIELDS)
        for column, cleaned_column in targets.items():
            old = (row.get(column) or "").strip()
            new = _fec_value(filing, cleaned_column)
            if old != new:
                result.changes.append((name, column, old, new))
                row[column] = new
    return result


# keep the file's own line endings
def _line_terminator(path: Path) -> str:
    if path.exists() and b"\r\n" in path.read_bytes():
        return "\r\n"
    return "\n"


# read roster rows and field names
def read_roster(path: Path) -> tuple[list[dict], list[str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), list(reader.fieldnames or [])


# write roster rows, keeping line endings
def write_roster(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    terminator = _line_terminator(path)
    temp = path.with_suffix(path.suffix + ".tmp")  # failed write leaves the roster intact
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator=terminator)
        writer.writeheader()
        writer.writerows(rows)
    replace_file(str(temp), str(path))


# sync both rosters; check=True only diffs
def sync_rosters(check: bool = False, cleaned_csv: Path = CLEANED_CSV,
                 roster_dir: Path = ROSTER_DIR) -> list[SyncResult]:
    cleaned = pd.read_csv(cleaned_csv, dtype=str, keep_default_na=False,
                          usecols=_CLEANED_COLUMNS, low_memory=False)
    latest = latest_filings(cleaned)
    results = []
    for filename, prefix in ROSTERS.items():
        path = roster_dir / filename
        if not path.exists():
            continue
        rows, fieldnames = read_roster(path)
        result = sync_rows(rows, fieldnames, prefix, latest, filename)
        if not check and (result.changes or result.fieldnames != fieldnames):
            write_roster(path, result.rows, result.fieldnames)
        results.append(result)
    return results


# print changes, return the exit code
def report(results: list[SyncResult], check: bool) -> int:
    drifted = False
    for r in results:
        linked = len(r.rows) - len(r.unlinked)
        verb = "would change" if check else "updated"
        print(f"{r.filename}: {len(r.rows)} rows, {linked} linked to FEC filings, "
              f"{len(r.unlinked)} editorial-only; {len(r.changes)} fields {verb}")
        for name, column, old, new in r.changes:
            print(f"  {name}: {column}: {old!r} -> {new!r}")
        if r.unlinked:
            print(f"  not in FEC data (address kept, street in pipeline style): {', '.join(r.unlinked)}")
        for name, key in r.broken:
            print(f"  BROKEN: {name}: donor_key {key!r} is in no cleaned filing but create_if_missing=false")
        drifted |= bool(r.changes)
    broken = any(r.broken for r in results)
    return 1 if broken or (check and drifted) else 0
