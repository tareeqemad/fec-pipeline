"""FEC's own entity type and contributor id: pulled, and filled in for rows already pulled."""
import csv

import fec.pull as pull
from fec.pull_rows import COLUMNS, build_row

OLD_COLUMNS = [c for c in COLUMNS if not c.startswith("fec_")]


def _raw(sub_id, name="AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE", tx="SA11AI.1", date="2024-10-18", amount="11000000.00"):
    return {"sub_id": sub_id, "transaction_id": tx, "two_year_transaction_period": "2024", "committee_id": "C00797670",
            "contributor_name": name, "contribution_receipt_date": date, "contribution_receipt_amount": amount,
            "is_individual": "True"}


def _result(sub_id, entity="ORG", contributor_id="", **raw):
    return {**_raw(sub_id, **raw), "entity_type": entity, "contributor_id": contributor_id}


def test_a_pulled_row_keeps_fec_entity_type_and_contributor_id():
    row = dict(zip(COLUMNS, build_row(_result("1", entity="org", contributor_id="C00000935"))))
    assert row["fec_entity_type"] == "ORG" and row["fec_contributor_id"] == "C00000935"
    assert row["is_individual"] == "True"  # the raw flag stays as FEC sent it


def test_an_older_file_gains_the_columns_and_nothing_else(tmp_path):
    path = tmp_path / "contributions.csv"
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OLD_COLUMNS, restval="")
        writer.writeheader()
        writer.writerow(_raw("1"))
    assert pull.ensure_columns(path) == 1
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    assert list(rows[0]) == COLUMNS
    assert rows[0]["contributor_name"] == "AMERICAN ISRAEL PUBLIC AFFAIRS COMMITTEE" and rows[0]["fec_entity_type"] == ""
    assert pull.ensure_columns(path) == 0  # already current


def test_backfill_matches_by_sub_id_then_one_exact_transaction_never_by_name():
    rows = [_raw("1"), _raw("2", tx="SA11AI.2", amount="800000"), _raw("3", name="DOE, JANE", tx="", amount="50"),
            _raw("9", tx="SA11AI.9")]
    rows[3]["committee_id"] = "C00710848"  # another committee: untouched
    results = [
        _result("1"),                                                # same sub_id
        _result("22", tx="SA11AI.2", amount="800000.00"),            # amended: new sub_id, same transaction
        _result("33", entity="IND", name="DOE, JANE", amount="50"),  # same name, no transaction id: no match
        _result("44", tx="SA11AI.4"),                                 # a new filing: not appended
    ]
    stats = pull.fill_source_fields(rows, results, "C00797670", 2024)
    assert [r.get("fec_entity_type", "") for r in rows] == ["ORG", "ORG", "", ""]
    assert rows[1]["sub_id"] == "2"  # the row keeps its own id
    assert stats["by_sub_id"] == 1 and stats["by_transaction"] == 1 and stats["unmatched"] == 1
    assert len(rows) == 4


def test_two_candidates_for_one_transaction_are_left_empty():
    rows = [_raw("2", tx="T1")]
    results = [_result("21", tx="T1"), _result("22", tx="T1", entity="IND")]
    stats = pull.fill_source_fields(rows, results, "C00797670", 2024)
    assert rows[0].get("fec_entity_type", "") == "" and stats["ambiguous"] == 1
