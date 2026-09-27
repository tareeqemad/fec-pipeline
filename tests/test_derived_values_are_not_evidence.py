"""A value taken from other filings is not evidence, and a value an override rejected is not reused."""
import pandas as pd

from fec.cleaning.audit_trail import AuditTrail
from fec.cleaning.donor_consistency import steps
from fec.donor_match.matcher import build_profiles


def _row(street, inferred):
    return {"contributor_name": "COHEN, ROBERT", "contributor_city": "LOS ANGELES", "contributor_state": "CA",
            "contributor_zip": "90024", "contributor_street_1": street, "contributor_employer": "",
            "occupation_category": "", "_generational_suffix": "", "_street_inferred": inferred}


def test_a_recovered_street_is_not_a_matching_street():
    profiles = build_profiles(pd.DataFrame([_row("1 MAIN ST", False), _row("9 ELM ST", True)]))
    (profile,) = profiles.values()
    assert profile["streets"] == {"1 MAIN ST"}


def test_a_value_an_override_rejected_is_put_back_after_every_step(monkeypatch):
    # step one refills the cleared employer; step two must not see it
    seen = []

    def refill(df):
        df.loc[0, "contributor_employer"] = "EYE SURGERY ASSOCIATES"
        return 1

    def look(df):
        seen.append(df.loc[0, "contributor_employer"])
        return 0

    def keep_cleared(df, company_names_only):
        df.loc[0, "contributor_employer"] = pd.NA
        return 1

    monkeypatch.setattr(steps, "CONSISTENCY_FIXES", (
        ("refill", refill, ("contributor_employer",), "r", None),
        ("look", look, ("contributor_employer",), "r", None),
    ))
    monkeypatch.setattr(steps, "apply_manual_employer_overrides", keep_cleared)
    df = pd.DataFrame({"sub_id": ["1"], "contributor_employer": [pd.NA]})
    steps.apply_donor_consistency(df, AuditTrail())
    assert len(seen) == 1 and pd.isna(seen[0])
