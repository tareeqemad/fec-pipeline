"""[CLEAR] in manual_employer_overrides.csv removes a filed value that is not the donor's."""
import pandas as pd

from fec.cleaning import manual_overrides


def test_clear_removes_another_persons_details_from_one_filing(tmp_path, monkeypatch):
    overrides = tmp_path / "manual_employer_overrides.csv"
    pd.DataFrame([{
        "sub_id": "1", "contributor_employer": "[CLEAR]", "contributor_occupation": "[CLEAR]",
        "contributor_city": "", "note": "another person's details", "previous_employer": "",
        "contributor_street_1": "[CLEAR]", "contributor_street_2": "", "contributor_zip": "", "source": "x",
    }]).to_csv(overrides, index=False)
    monkeypatch.setattr(manual_overrides, "OVERRIDES_CSV", overrides)
    df = pd.DataFrame({
        "sub_id": ["1", "2"],
        "contributor_employer": ["HACKMAN CAPITAL", "KTBS LAW LLP"],
        "contributor_occupation": ["REAL ESTATE INVESTOR", "ATTORNEY"],
        "contributor_street_1": ["4060 INCE BLVD.", "1801 CENTURY PARK E"],
        "contributor_city": ["CULVER CITY", "LOS ANGELES"],
    })

    assert manual_overrides.apply_manual_employer_overrides(df) == 1
    assert df.loc[0, ["contributor_employer", "contributor_occupation", "contributor_street_1"]].isna().all()
    assert df.loc[0, "contributor_city"] == "CULVER CITY"  # a blank cell changes nothing
    assert df.loc[1, "contributor_employer"] == "KTBS LAW LLP"
