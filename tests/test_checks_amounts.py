"""A missing or unreadable amount is reported, never written back as $0."""
import pandas as pd
import pytest

from fec.cleaning.quality.gates import run_quality_gates
from fec.resolve.pipeline import cli


def _write(tmp_path, amounts):
    path = tmp_path / "contributions_cleaned.csv"
    pd.DataFrame({
        "sub_id": [str(1000 + i) for i in range(len(amounts))],
        "donor_key": ["D1"] * len(amounts),
        "contribution_receipt_amount": amounts,
    }).to_csv(path, index=False)
    return str(path)


def test_resolve_stops_on_a_missing_amount_instead_of_writing_zero(tmp_path):
    with pytest.raises(ValueError, match="1 row.*sub_id 1001"):
        cli._load_data(_write(tmp_path, ["250", "", "100"]))


def test_resolve_stops_on_an_unreadable_amount(tmp_path):
    with pytest.raises(ValueError, match="sub_id 1000"):
        cli._load_data(_write(tmp_path, ["$250", "100"]))


def test_readable_amounts_load_as_numbers(tmp_path):
    _dir, df, *_rest = cli._load_data(_write(tmp_path, ["250", "-50.5"]))
    assert df["contribution_receipt_amount"].tolist() == [250.0, -50.5]


def test_quality_gate_fails_on_a_missing_amount():
    report = run_quality_gates(pd.DataFrame({"contribution_receipt_amount": ["250", None, "abc"]}))

    assert report["checks"]["amounts_readable"] == {"passed": False, "count": 2}
    assert report["passed"] is False
