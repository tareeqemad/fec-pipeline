import csv

import pandas as pd
import pytest

from fec.pull_rows import COLUMNS, SNAPSHOT_COLUMNS, SOURCE_FIELDS, source_values
from fec.receipt_types import classify_receipts
from fec.receipts import read_current_receipts


def test_receipts_classified_by_report_line_and_memo():
    fields = ("fec_filing_form", "fec_line_number", "fec_memo_code")
    cases = [
        *(("F3X", line, "", "contribution") for line in ("11AI", "11B", "11C")),
        *(("F3X", line, "", "other_receipt") for line in ("12", "13", "14", "17")),
        ("F3X", "15", "", "operating_offset"), ("F3X", "16", "", "contribution_refund"),
        ("F3X", "11AI", "X", "memo"), ("F3X", "17", "X", "memo"),
        ("F3X", "11AI", "?", "unclassified"), ("F3X", "99", "", "unclassified"),
        ("F3X", "15", "Y", "unclassified"), ("F3", "15", "", "unclassified"),
        ("", "", "", "unclassified"),
    ]
    sample = pd.DataFrame([case[:3] for case in cases], columns=fields, index=range(50, 50 + len(cases)))
    result = classify_receipts(sample)
    assert result.index.equals(sample.index)
    assert result.tolist() == [case[3] for case in cases]


@pytest.mark.parametrize(("source", "memo"), [
    ({}, "?"), ({"memo_code": None}, ""), ({"memo_code": " x "}, "X"),
    ({"memoed_subtotal": False}, ""), ({"memoed_subtotal": True, "memo_code": ""}, "X"),
    ({"memo_code": "Y"}, "Y"),
])
def test_latest_receipt_memo_information(source, memo):
    result = source_values({"filing_form": "f3x", "line_number": "11ai", **source})
    assert (result["fec_filing_form"], result["fec_line_number"], result["fec_memo_code"]) == ("F3X", "11AI", memo)


def test_old_snapshot_uses_both_ids_and_new_snapshot_is_authoritative(tmp_path):
    def write(path, columns, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(records)

    old_columns = [*COLUMNS, "fec_entity_type", "fec_contributor_id", "fec_image_number"]
    source_columns = ["sub_id", "fec_image_number", "fec_filing_form", "fec_line_number", "fec_memo_code", "source"]
    raw = tmp_path / "contributions.csv"
    scope = tmp_path / "snapshots" / "C00000001-2026"
    first = {
        "sub_id": "4100000000000000001", "committee_id": "C00000001", "two_year_transaction_period": "2026",
        "transaction_id": "0001", "fec_image_number": "202604149857195889",
    }
    second = {**first, "sub_id": "4100000000000000002", "transaction_id": "0002", "fec_image_number": "202604149857195890"}
    write(scope / "01.csv", old_columns, [first, second])
    source = {
        "sub_id": first["sub_id"], "fec_image_number": first["fec_image_number"],
        "fec_filing_form": "F3X", "fec_line_number": "15", "fec_memo_code": "",
        "source": "https://docquery.fec.gov/dcdev/posted/2000000.fec",
    }
    write(tmp_path / "fec_receipt_lines.csv", source_columns, [source, {**source, "sub_id": second["sub_id"]}])
    previous = read_current_receipts(raw).set_index("sub_id")
    assert classify_receipts(previous).to_dict() == {first["sub_id"]: "operating_offset", second["sub_id"]: "unclassified"}
    # Older published snapshots with line fields override any earlier sidecar evidence.
    write(scope / "02.csv", [*COLUMNS, *SOURCE_FIELDS],
          [first, {**second, "fec_filing_form": "F3X", "fec_line_number": "11C", "fec_memo_code": ""}])
    current = read_current_receipts(raw).set_index("sub_id")
    assert classify_receipts(current).to_dict() == {first["sub_id"]: "unclassified", second["sub_id"]: "contribution"}

    write(scope / "03.csv", SNAPSHOT_COLUMNS, [
        {**first, "receipt_kind": "unclassified"}, {**second, "receipt_kind": "memo"},
    ])
    current = read_current_receipts(raw).set_index("sub_id")
    assert current["receipt_kind"].to_dict() == {first["sub_id"]: "unclassified", second["sub_id"]: "memo"}
    assert "fec_line_number" not in current
    write(scope / "04.csv", SNAPSHOT_COLUMNS, [{**first, "receipt_kind": "typo"}])
    with pytest.raises(ValueError, match="receipt_kind"):
        read_current_receipts(raw)
