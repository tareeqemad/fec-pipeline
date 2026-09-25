"""The audit trail tells which rows a step's change survived on."""
import pandas as pd

from fec.cleaning.audit_trail import AuditTrail


def _fill(df):
    df.loc[df["sub_id"] == "1", "contributor_employer"] = "OPTUM"
    return 1


def _undo(df):
    df.loc[df["sub_id"] == "2", "contributor_employer"] = ""
    return 1


def test_keys_set_by_lists_rows_whose_change_survives():
    df = pd.DataFrame({"sub_id": ["1", "2", "3"], "contributor_employer": ["", "", "ACME"]})
    trail = AuditTrail()
    trail.start(df)
    trail.run(df, _fill, "donor_fill_employer_from_donor", "r", ("contributor_employer",))
    df.loc[df["sub_id"] == "2", "contributor_employer"] = "X"
    trail.run(df, lambda frame: 0, "noop", "r", ("contributor_employer",))
    trail.run(df, _undo, "donor_fill_employer_from_donor", "r", ("contributor_employer",))

    assert trail.keys_set_by({"donor_fill_employer_from_donor"}, {"contributor_employer"}) == {"1"}
