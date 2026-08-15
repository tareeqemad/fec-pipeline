"""Per-sub_id hand-curated corrections to employer and/or occupation."""
import csv

import pandas as pd
import pytest

import fec.cleaning.manual_overrides as mo


@pytest.fixture
def override_file(tmp_path, monkeypatch):
    def _write(rows, cols=("sub_id", "contributor_employer", "contributor_occupation", "note")):
        p = tmp_path / "overrides.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cols))
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols})
        monkeypatch.setattr(mo, "OVERRIDES_CSV", p)
        return p
    return _write


def _frame():
    return pd.DataFrame({
        "sub_id": ["1", "2", "3"],
        "contributor_employer": ["CHAIRMAN", "OLD EMPLOYER", "UNTOUCHED"],
        "contributor_occupation": ["KIMCO", "ATTORNEY", "ENGINEER"],
    })


def test_both_fields_can_be_overridden(override_file):
    """The swap case: employer and occupation corrected together."""
    override_file([{"sub_id": "1", "contributor_employer": "KIMCO REALTY",
                    "contributor_occupation": "CHAIRMAN"}])
    df = _frame()

    assert mo.apply_manual_employer_overrides(df) == 1
    assert df.loc[0, "contributor_employer"] == "KIMCO REALTY"
    assert df.loc[0, "contributor_occupation"] == "CHAIRMAN"
    # other rows untouched
    assert df.loc[2, "contributor_employer"] == "UNTOUCHED"


def test_employer_only_leaves_occupation_alone(override_file):
    """Occupation must not be blanked."""
    override_file([{"sub_id": "2", "contributor_employer": "NEW EMPLOYER"}])
    df = _frame()

    assert mo.apply_manual_employer_overrides(df) == 1
    assert df.loc[1, "contributor_employer"] == "NEW EMPLOYER"
    assert df.loc[1, "contributor_occupation"] == "ATTORNEY"


def test_occupation_only(override_file):
    override_file([{"sub_id": "3", "contributor_occupation": "SOFTWARE ENGINEER"}])
    df = _frame()

    assert mo.apply_manual_employer_overrides(df) == 1
    assert df.loc[2, "contributor_occupation"] == "SOFTWARE ENGINEER"
    assert df.loc[2, "contributor_employer"] == "UNTOUCHED"


def test_final_company_pass_does_not_restore_status_or_occupation(override_file):
    override_file([
        {
            "sub_id": "1",
            "contributor_employer": "REAL COMPANY LLC",
            "contributor_occupation": "CHAIRMAN",
        },
        {
            "sub_id": "2",
            "contributor_employer": "SELF-EMPLOYED",
            "contributor_occupation": "OWNER",
        },
    ])
    df = _frame()
    df.loc[1, "contributor_employer"] = "RETIRED"

    assert mo.apply_manual_employer_overrides(
        df, company_names_only=True,
    ) == 1
    assert df.loc[0, "contributor_employer"] == "REAL COMPANY LLC"
    assert df.loc[0, "contributor_occupation"] == "KIMCO"
    assert df.loc[1, "contributor_employer"] == "RETIRED"


def test_final_pass_can_preserve_previous_self_employment(override_file):
    override_file([{
        "sub_id": "2",
        "contributor_employer": "SELF-EMPLOYED",
        "previous_employer": "SELF-EMPLOYED",
    }], cols=("sub_id", "contributor_employer", "note", "previous_employer"))
    df = _frame()
    df["previous_employer"] = ""
    df.loc[1, "contributor_employer"] = "RETIRED"

    assert mo.apply_manual_employer_overrides(
        df, company_names_only=True,
    ) == 1
    assert df.loc[1, "contributor_employer"] == "RETIRED"
    assert df.loc[1, "previous_employer"] == "SELF-EMPLOYED"


def test_previous_employer_can_be_explicitly_cleared(override_file):
    override_file([{
        "sub_id": "2",
        "previous_employer": "[CLEAR]",
    }], cols=("sub_id", "note", "previous_employer"))
    df = _frame()
    df["previous_employer"] = ["", "WRONG COMPANY", ""]

    assert mo.apply_manual_employer_overrides(
        df, company_names_only=True,
    ) == 1
    assert df.loc[1, "previous_employer"] == ""


def test_legacy_file_without_the_occupation_column_still_loads(override_file):
    override_file([{"sub_id": "2", "contributor_employer": "NEW EMPLOYER"}],
                  cols=("sub_id", "contributor_employer", "note"))
    df = _frame()

    assert mo.apply_manual_employer_overrides(df) == 1
    assert df.loc[1, "contributor_employer"] == "NEW EMPLOYER"


def test_unknown_sub_id_is_not_an_error(override_file):
    override_file([{"sub_id": "999", "contributor_employer": "GHOST"}])
    df = _frame()

    assert mo.apply_manual_employer_overrides(df) == 0
    assert "GHOST" not in df["contributor_employer"].tolist()


def test_shipped_override_file_is_wellformed():
    """The real file must parse and every row must carry a sub_id + a value."""
    if not mo.OVERRIDES_CSV.exists():
        pytest.skip("no override file shipped")
    with mo.OVERRIDES_CSV.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows, "override file is empty"
    for r in rows:
        assert (r.get("sub_id") or "").strip(), r
        assert ((r.get("contributor_employer") or "").strip()
                or (r.get("contributor_occupation") or "").strip()
                or (r.get("contributor_city") or "").strip()
                or (r.get("previous_employer") or "").strip()), r


def test_no_override_reinstates_a_known_truncation():
    """Overrides run last, so an override whose value is a known truncation silently undoes the repair."""
    from fec.env import EMPLOYER_NAME_RULES_CSV

    with EMPLOYER_NAME_RULES_CSV.open(encoding="utf-8", newline="") as handle:
        truncations = {
            row["variant"].strip().upper()
            for row in csv.DictReader(handle)
            if row["source"] == "manual"
        }
    with open("data/manual_employer_overrides.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    clashes = [
        (r["sub_id"], r["contributor_employer"])
        for r in rows
        if (r.get("contributor_employer") or "").strip().upper() in truncations
    ]
    assert not clashes, f"overrides reinstate a repaired truncation: {clashes}"


def test_greglevine_domain_is_kept_as_a_company():
    """The reported domain belongs to an active Florida corporation."""
    with open("data/manual_employer_overrides.csv", encoding="utf-8", newline="") as f:
        rows = {row["sub_id"]: row for row in csv.DictReader(f)}

    assert rows["4011420251130090696"]["contributor_employer"] == "GREGLEVINE.COM INC"


def test_robert_namoff_student_filing_is_repaired():
    """Official records confirm he chaired the Miami chemical company."""
    with open("data/manual_employer_overrides.csv", encoding="utf-8", newline="") as f:
        rows = {row["sub_id"]: row for row in csv.DictReader(f)}

    row = rows["4062420241962022446"]
    assert row["contributor_employer"] == "ALLIED UNIVERSAL CORP"
    assert row["contributor_occupation"] == "CHAIRMAN"
