import csv

import pytest

import clean
from fec.cleaning.reports.gates import run_quality_gates
from fec.contract import CLEAN_COLUMNS, LOADED_COLUMNS, check_input
from fec.database.loader import validate
from fec.database.loader.contributions import _native_row
from fec.geocoding.cache import GeoCache
from fec.io import read_pipeline_csv
from fec.pull_rows import COLUMNS, SOURCE_COLUMNS, SOURCE_FIELDS
from fec.receipts import read_current_receipts
from fec.resolve import pipeline as resolve
from geocode import _geocode_contributors, _write_output


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_snapshot_fields_are_authoritative_and_preserve_long_ids(tmp_path):
    path = tmp_path / "contributions.csv"
    first = {"sub_id": "4100000000000000001", "committee_id": "C00000001",
             "two_year_transaction_period": "2026", "transaction_id": "001"}
    second = {**first, "sub_id": "4100000000000000002", "committee_id": "C00000002"}
    write_csv(path, COLUMNS, [first, second])
    write_csv(tmp_path / "fec_source_fields.csv", SOURCE_COLUMNS, [
        {"sub_id": row["sub_id"], "fec_entity_type": "ORG"} for row in (first, second)
    ])
    current = {**second, "fec_image_number": "202604149857195889"}
    snapshot = tmp_path / "snapshots" / "C00000002-2026" / "01.csv"
    write_csv(snapshot, [*COLUMNS, *SOURCE_FIELDS], [current])
    receipts = read_current_receipts(path)
    assert receipts["sub_id"].tolist() == [first["sub_id"], second["sub_id"]]
    assert receipts["transaction_id"].tolist() == ["001", "001"]
    assert receipts["fec_entity_type"].tolist() == ["ORG", ""]
    assert receipts["fec_image_number"].tolist() == ["", current["fec_image_number"]]

    write_csv(snapshot, [*COLUMNS, *SOURCE_FIELDS], [first])
    with pytest.raises(ValueError, match="outside"):
        read_current_receipts(path)
    write_csv(snapshot, [*COLUMNS, *SOURCE_FIELDS], [second, second])
    with pytest.raises(ValueError, match="duplicate sub_id"):
        read_current_receipts(path)


def test_header_only_current_snapshot_can_clear_the_entire_dataset(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshots" / "C00000002-2026" / "01.csv"
    write_csv(snapshot, [*COLUMNS, *SOURCE_FIELDS], [])
    output = tmp_path / "cleaned.csv"
    reports = tmp_path / "reports"
    monkeypatch.setattr(clean, "RAW_CSV", tmp_path / "contributions.csv")
    monkeypatch.setattr(clean, "CLEANED_CSV", output)
    monkeypatch.setattr(clean, "REPORTS_DIR", reports)
    monkeypatch.setattr(clean, "DONOR_KEYS_CSV", tmp_path / "keys.csv")
    monkeypatch.setattr(resolve, "REPORTS_DIR", reports)
    monkeypatch.setattr(validate, "CLEANED_CSV", output)
    clean.main()
    frame = read_pipeline_csv(output)
    columns = list(frame.columns)
    geo = _geocode_contributors(frame, GeoCache(str(tmp_path / "geocache.json")))
    _write_output(geo, str(output), columns)
    geo = read_pipeline_csv(output)
    resolve._write_results(geo, str(output), list(geo.columns))
    final, _ = validate._read_input()
    assert final.empty and set(final.columns) == set(LOADED_COLUMNS)
    assert run_quality_gates(final)["passed"]


def test_paper_filing_without_transaction_id_loads_as_sql_null():
    values = ("4100000000000000002", None, 1, 1, None, None, 25, None, 2026, "unclassified", True)
    assert _native_row(values)[1] is None


def test_older_cleaned_file_still_passes_the_next_stage_without_a_verified_line(tmp_path):
    path = tmp_path / "old-cleaned.csv"
    write_csv(path, [field for field in CLEAN_COLUMNS if field != "receipt_kind"], [{"sub_id": "1"}])
    frame = read_pipeline_csv(path)
    assert frame["receipt_kind"].tolist() == ["unclassified"]
    check_input("geocode", frame.columns)
    check_input("resolve", frame.columns)
