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


def test_backfill_matches_by_sub_id_then_one_exact_transaction_never_by_name():
    rows = [_raw("1"), _raw("2", tx="SA11AI.2", amount="800000"), _raw("3", name="DOE, JANE", tx="", amount="50"),
            _raw("9", tx="SA11AI.9")]
    rows[3]["committee_id"] = "C00710848"  # another committee: untouched
    before = [dict(row) for row in rows]
    results = [
        _result("1"),                                                # same sub_id
        _result("22", tx="SA11AI.2", amount="800000.00"),            # amended: new sub_id, same transaction
        _result("33", entity="IND", name="DOE, JANE", amount="50"),  # same name, no transaction id: no match
        _result("44", tx="SA11AI.4"),                                 # a new filing: not added
    ]
    records, stats = pull.match_source_fields(rows, results, "C00797670", 2024)
    assert rows == before  # the raw rows are only read
    assert set(records) == {"1", "2"}
    assert records["1"]["matched_by"] == "sub_id" and records["1"]["fec_sub_id"] == ""
    assert records["2"]["matched_by"] == "transaction" and records["2"]["fec_sub_id"] == "22"
    assert records["2"]["fec_entity_type"] == "ORG"
    assert stats["by_sub_id"] == 1 and stats["by_transaction"] == 1 and stats["unmatched"] == 1


def test_two_candidates_for_one_transaction_are_left_out():
    records, stats = pull.match_source_fields([_raw("2", tx="T1")], [_result("21", tx="T1"), _result("22", tx="T1")],
                                              "C00797670", 2024)
    assert records == {} and stats["ambiguous"] == 1


def test_source_file_keeps_other_rows_and_replaces_rematched_ones(tmp_path):
    path = tmp_path / "fec_source_fields.csv"
    pull.append_source_fields(path, [pull._source_record("1", _result("1", entity="IND"), "pulled"),
                                     pull._source_record("5", _result("5"), "pulled")])
    saved = pull.read_source_fields(path)
    pull.write_source_fields(path, {**saved, "1": pull._source_record("1", _result("1"), "sub_id")})
    rows = {row["sub_id"]: row for row in csv.DictReader(open(path, encoding="utf-8"))}
    assert rows["1"]["fec_entity_type"] == "ORG" and rows["5"]["fec_entity_type"] == "ORG"
    assert pull.source_path(tmp_path / "contributions.csv") == path
