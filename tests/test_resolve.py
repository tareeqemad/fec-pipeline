import pandas as pd
import pytest

from fec.resolve.apply import apply_results
from fec.resolve import previous


@pytest.mark.parametrize(("entity_type", "employer", "previous", "status"), [
    ("INDIVIDUAL", "RETIRED", "SELF-EMPLOYED", "retired"),
    ("ORGANIZATION", "", "", "organization"),
])
def test_resolve_without_working_filings(entity_type, employer, previous, status):
    filings = pd.DataFrame([{
        "sub_id": "1", "donor_key": "D1", "identity_status": "confirmed",
        "entity_type": entity_type, "contributor_employer": employer,
        "previous_employer": previous, "contribution_receipt_date": "2024-06-01",
        "contributor_street_1": "1 MAIN ST", "contributor_zip": "94104",
    }])
    row = apply_results(filings).iloc[0]
    assert row["employer_status"] == status
    assert row["previous_employer"] == previous


@pytest.mark.parametrize(("answer", "expected", "other"), [("ACME", "ACME", ""), ("[CLEAR]", "", "OTHER FIRM")])
def test_filing_previous_employer_stays_scoped_on_rerun(tmp_path, monkeypatch, answer, expected, other):
    by_donor = tmp_path / "previous_employers.csv"
    by_donor.write_text(f"sub_id,previous_employer\n1,{other}\n")
    by_filing = tmp_path / "filing_previous_employers.csv"
    by_filing.write_text(f"sub_id,previous_employer\n2,{answer}\n3,{answer}\n")
    monkeypatch.setattr(previous, "PREVIOUS_EMPLOYERS_CSV", by_donor)
    monkeypatch.setattr(previous, "FILING_PREVIOUS_EMPLOYERS_CSV", by_filing)
    df = pd.DataFrame({
        "sub_id": ["1", "2", "3"], "donor_key": ["D1"] * 3, "entity_type": ["INDIVIDUAL"] * 3,
        "identity_status": ["confirmed"] * 3, "previous_employer": [""] * 3,
        "contributor_employer": ["RETIRED", "RETIRED", "NOT EMPLOYED"],
        "contribution_receipt_date": ["2022-01-01", "2025-06-01", "2026-01-01"],
        "contributor_street_1": ["1 MAIN ST"] * 3, "contributor_zip": ["94104"] * 3,
    })
    for _ in range(2):
        df = apply_results(df)
        assert df.previous_employer.tolist() == [other, expected, ""]
