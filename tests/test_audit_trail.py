import numpy as np
import pandas as pd

from fec.cleaning.audit_trail import UNTRACKED_STEP, AuditTrail, summarize


def _frame():
    return pd.DataFrame({
        "sub_id": ["1", "2"],
        "contributor_name": ["SMITH, JOHN", "DOE, JANE"],
        "contributor_employer": ["SELF", "ACME"],
        "contributor_zip": ["10022-1234", "01367"],
    })


def test_records_changes_with_source():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    def change(frame):
        frame.loc[0, "contributor_name"] = "SMITH, JONATHAN"
        frame.loc[0, "contributor_employer"] = "SELF-EMPLOYED"
        return 2

    trail.run(
        df,
        change,
        "clean_person",
        "curated_correction",
        ("contributor_name", "contributor_employer"),
        source="data/rules.csv",
    )

    assert trail.finish(df) == 0
    assert [
        (row["field"], row["before"], row["after"], row["source"])
        for row in trail.net_records()
    ] == [
        ("contributor_name", "SMITH, JOHN", "SMITH, JONATHAN", "data/rules.csv"),
        ("contributor_employer", "SELF", "SELF-EMPLOYED", "data/rules.csv"),
    ]


def test_flags_untracked_change_between_steps():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)
    df.loc[1, "contributor_employer"] = "BETA"

    def later(frame):
        frame.loc[1, "contributor_employer"] = "BETA INC"
        return 1

    trail.run(df, later, "later", "suffix", ("contributor_employer",))

    assert [(row["step"], row["before"], row["after"]) for row in trail.records] == [
        (UNTRACKED_STEP, "ACME", "BETA"),
        ("later", "BETA", "BETA INC"),
    ]
    assert trail.finish(df) == 0


def test_finish_finds_untracked_final_value():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)
    df.loc[0, "contributor_zip"] = "10022"

    assert trail.finish(df) == 1
    assert trail.records[0]["step"] == UNTRACKED_STEP


def test_new_column_becomes_baseline():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    def create(frame):
        frame["occupation_category"] = ["LEGAL", "OTHER"]
        return 2

    trail.run(df, create, "categorize", "derived", ("occupation_category",))
    assert trail.records == []

    df.loc[0, "occupation_category"] = "FINANCE / INVESTMENT"
    assert trail.finish(df) == 1


def test_per_row_reason_and_source():
    df = _frame()
    df["_reason"] = ["typo", "manual"]
    df["_source"] = [np.nan, "https://example.com"]
    trail = AuditTrail()
    trail.start(df)

    def change(frame):
        frame["contributor_name"] = ["SMITH, JON", "DOE, JANET"]
        return 2

    trail.run(
        df,
        change,
        "names",
        lambda frame: frame["_reason"],
        ("contributor_name",),
        source=lambda frame: frame["_source"],
    )

    assert [(row["reason"], row["source"]) for row in trail.records] == [
        ("typo", None),
        ("manual", "https://example.com"),
    ]


def test_undone_changes_are_removed_and_summary_is_simple():
    df = _frame()
    trail = AuditTrail()
    trail.start(df)

    trail.run(
        df,
        lambda frame: frame.__setitem__("contributor_employer", [np.nan, "ACME"]),
        "clear",
        "placeholder",
        ("contributor_employer",),
    )
    trail.run(
        df,
        lambda frame: frame.__setitem__("contributor_employer", ["SELF", "ACME"]),
        "restore",
        "restored",
        ("contributor_employer",),
    )

    assert trail.net_records() == []
    assert summarize(trail.net_records()) == {}
