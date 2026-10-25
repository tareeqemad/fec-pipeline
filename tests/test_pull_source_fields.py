"""FEC's own entity type and contributor id go to fec_source_fields.csv; the raw file is never rewritten."""
import csv

import fec.pull as pull
from fec.pull_rows import COLUMNS, build_row


def _raw(sub_id, name="AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE", tx="SA11AI.1", date="2024-10-18", amount="11000000.00"):
    return {"sub_id": sub_id, "transaction_id": tx, "two_year_transaction_period": "2024", "committee_id": "C00797670",
            "contributor_name": name, "contribution_receipt_date": date, "contribution_receipt_amount": amount,
            "is_individual": "True"}


def _result(sub_id, entity="ORG", contributor_id="", **raw):
    return {**_raw(sub_id, **raw), "entity_type": entity, "contributor_id": contributor_id}


def test_the_raw_row_keeps_its_original_columns():
    row = build_row(_result("1", contributor_id="C00000935"))
    assert len(row) == len(COLUMNS) and not any(c.startswith("fec_") for c in COLUMNS)


def test_source_file_keeps_other_rows_when_appending(tmp_path):
    path = tmp_path / "fec_source_fields.csv"
    first = pull._source_record("1", {**_result("1", entity="IND"), "image_number": "202410189698745485"})
    second = pull._source_record("5", _result("5", contributor_id="C00000935"))
    pull.append_source_fields(path, [first])
    pull.append_source_fields(path, [second])
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == pull.SOURCE_COLUMNS
        assert list(reader) == [first, second]
    assert first["fec_entity_type"] == "IND" and first["fec_image_number"] == "202410189698745485"
    assert second["fec_entity_type"] == "ORG" and second["fec_contributor_id"] == "C00000935"
    assert pull.source_path(tmp_path / "contributions.csv") == path
