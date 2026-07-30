"""clean.py writes quality_scan.json each run; the scan is detect-only."""
import json

import pandas as pd

from fec.cleaning import cli


def test_write_quality_scan(tmp_path):
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL", "INDIVIDUAL"],
        "contributor_employer": ["ACME MGMT", "ACME MANAGEMENT"],
        "contributor_occupation": ["X", "Y"],
        "contributor_first_name": ["JOHN", "JANE"],
        "contributor_last_name": ["DOE", "ROE"],
        "contributor_name": ["DOE, JOHN", "ROE, JANE"],
        "donor_key": ["a", "b"],
        "contributor_zip": ["10001", "10002"],
        "contributor_street_1": ["1 MAIN ST", "2 OAK AVE"],
    })
    out = tmp_path / "contributions_cleaned.csv"
    df.to_csv(out, index=False)

    report = cli._write_quality_scan(str(out), str(tmp_path))

    written = json.loads((tmp_path / "quality_scan.json").read_text(encoding="utf-8"))
    assert written == report
    for k in ("rows", "employer_abbreviations", "employer_near_duplicates",
              "name_composite_drift", "address_order_variants"):
        assert k in report
    # ACME MGMT vs ACME MANAGEMENT collapse to one fingerprint -> a near-dup group.
    assert report["employer_near_duplicates"]["groups"] >= 1
