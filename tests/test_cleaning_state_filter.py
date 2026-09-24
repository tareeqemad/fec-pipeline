"""No filing is dropped for its state: a Canadian 'ON' is kept for the foreign step."""
import pandas as pd

from fec.cleaning.pipeline.core import _prepare_records


def test_a_filing_with_a_foreign_state_is_kept():
    df = pd.DataFrame({
        "sub_id": ["1", "2", "3"],
        "contributor_name": ["DOE, JANE", "ROE, JOHN", "POE, ANN"],
        "contributor_state": ["ny", "ON", None],
        "contributor_occupation": ["CEO", "CEO", "CEO"],
        "contributor_employer": ["ACME", "ACME", "ACME"],
        "contribution_receipt_date": ["2024-01-01"] * 3,
        "contribution_receipt_amount": [100, 200, 300],
        "is_individual": ["true"] * 3,
    })

    out = _prepare_records(df, lambda message: None)

    assert out["sub_id"].tolist() == ["1", "2", "3"]
    assert out["contributor_state"].tolist()[:2] == ["NY", "ON"]
    assert pd.isna(out["contributor_state"].iloc[2])
