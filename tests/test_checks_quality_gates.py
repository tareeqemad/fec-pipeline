"""quality_gates.json must describe the final (resolved) file, and final_verify must fail
when a gate failed or never ran."""
import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from fec.cleaning.occupations import _categorize_final
from fec.cleaning.employer_status import classify_employer_statuses
from fec.cleaning.quality import run_quality_gates
from fec.resolve.pipeline import cli as resolve_cli

REPO = Path(__file__).resolve().parents[1]


def _final_verify():
    spec = importlib.util.spec_from_file_location("final_verify", REPO / "tools" / "audits" / "final_verify.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolved_frame():
    df = pd.DataFrame({
        "sub_id": ["1", "2"],
        "entity_type": ["INDIVIDUAL", "INDIVIDUAL"],
        "contributor_name": ["DOE, JANE", "ROE, RICHARD"],
        "contributor_employer": ["ACME", "RETIRED"],
        "contributor_occupation": ["ENGINEER", "RETIRED"],
        "previous_employer": ["", "BOEING"],
        "employer_city": ["ST LOUIS", ""],
    })
    df["occupation_category"] = _categorize_final(df["contributor_occupation"]).to_numpy()
    df["employer_status"] = classify_employer_statuses(df).to_numpy()
    return df


def _clean_stage_frame():
    return _resolved_frame().drop(columns=["employer_status", "previous_employer", "employer_city"])


def test_resolve_writes_the_final_gate_report(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions_cleaned.csv"
    (tmp_path / "quality_gates.json").write_text(
        json.dumps({**run_quality_gates(_clean_stage_frame()), "stage": "clean"}), encoding="utf-8")
    monkeypatch.setattr(resolve_cli, "apply_results", lambda df, *_caches: df)

    resolve_cli._write_results(_resolved_frame(), str(csv_path), None, None)

    report = json.loads((tmp_path / "quality_gates.json").read_text(encoding="utf-8"))
    assert report["stage"] == "resolve" and report["csv_written"] is True
    assert report["passed"] is True
    assert not [name for name, check in report["checks"].items()
                if isinstance(check, dict) and check.get("not_run")]
    for gate in ("self_employed_status_consistency", "not_employed_status_consistency",
                 "previous_employer_scope", "retired_active_sync"):
        assert report["checks"][gate]["passed"] is True
    assert report["checks"]["row_count"] == 2
    assert pd.read_csv(csv_path)["employer_city"].tolist()[0] == "SAINT LOUIS"
    assert not (tmp_path / "quality_gates.json.tmp").exists()
    assert _final_verify().quality_gate_problems(report, rows=2) == []


def test_resolve_records_a_failed_gate_and_writes_no_csv(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions_cleaned.csv"
    csv_path.write_text("untouched\n", encoding="utf-8")
    bad = _resolved_frame()
    bad.loc[0, "previous_employer"] = "OLD CO"          # previous employer on an active row
    monkeypatch.setattr(resolve_cli, "apply_results", lambda df, *_caches: df)

    with pytest.raises(ValueError, match="Previous employer on non-retired rows"):
        resolve_cli._write_results(bad, str(csv_path), None, None)

    report = json.loads((tmp_path / "quality_gates.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and report["csv_written"] is False
    assert csv_path.read_text(encoding="utf-8") == "untouched\n"
    problems = _final_verify().quality_gate_problems(report, rows=2)
    assert "previous_employer_scope: failed" in problems


def test_final_verify_fails_on_the_clean_stage_report():
    report = {**run_quality_gates(_clean_stage_frame()), "stage": "clean"}
    assert report["passed"] is True                     # clean's own verdict hides the gap
    problems = _final_verify().quality_gate_problems(report, rows=2)
    not_run = {p.split(":")[0] for p in problems if "not run" in p}
    assert {"self_employed_status_consistency", "not_employed_status_consistency",
            "previous_employer_scope", "retired_active_sync"} <= not_run


def test_final_verify_fails_on_a_stale_or_missing_report(tmp_path):
    verify = _final_verify()
    report = run_quality_gates(_resolved_frame())
    assert verify.quality_gate_problems(report, rows=2) == []
    assert verify.quality_gate_problems(report, rows=3) == ["report covers 2 rows, the cleaned file has 3"]
    assert verify.quality_gate_problems({"passed": True, "checks": {}}) != []

    assert verify.check_quality_gates(str(tmp_path / "nope.json")) == [f"{tmp_path / 'nope.json'} is missing"]
    path = tmp_path / "quality_gates.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert verify.check_quality_gates(str(path), rows=2) == []
