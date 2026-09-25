"""Tests for the proactive data-quality scanner."""

import pandas as pd

from fec.cleaning.quality import scan as qs


def _df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


def test_flags_employer_abbreviations():
    df = _df(
        [
            ["INDIVIDUAL", "EMA INV MGMT"],
            ["INDIVIDUAL", "KIMBER MFG"],
            ["INDIVIDUAL", "GOLDMAN SACHS"],
            ["INDIVIDUAL", "RETIRED"],
        ],
        ["entity_type", "contributor_employer"],
    )
    r = qs.scan_employer_abbreviations(df)
    assert "MGMT" in r["by_token"] and "MFG" in r["by_token"]
    assert r["distinct_employers_flagged"] == 2  # GOLDMAN/RETIRED not flagged


def test_near_duplicates_collapse_abbrev_but_split_different_firms():
    df = _df(
        [
            ["INDIVIDUAL", "ROBICO MANAGEMENT"],
            ["INDIVIDUAL", "ROBICO MGMT"],
            ["INDIVIDUAL", "M&R MANAGEMENT"],
            ["INDIVIDUAL", "S&A MANAGEMENT"],
        ],
        ["entity_type", "contributor_employer"],
    )
    r = qs.scan_employer_near_duplicates(df)
    # the MGMT/MANAGEMENT pair is one group
    assert any("ROBICO MANAGEMENT" in g and "ROBICO MGMT" in g for g in r["examples"])
    # M&R vs S&A keep their single letters and do not collide
    assert not any(
        "M&R MANAGEMENT" in g and "S&A MANAGEMENT" in g for g in r["examples"]
    )


def test_name_composite_drift_detected():
    df = _df(
        [
            ["INDIVIDUAL", "d1", "GOULD, FRED", "FREDERIC", "GOULD"],
            ["INDIVIDUAL", "d2", "DOE, JANE", "JANE", "DOE"],
        ],
        [
            "entity_type",
            "donor_key",
            "contributor_name",
            "contributor_first_name",
            "contributor_last_name",
        ],
    )
    r = qs.scan_name_composite_drift(df)
    assert r["rows"] == 1 and r["donors"] == 1  # d1 drifts, d2 matches


def test_address_order_variants_detected():
    df = _df(
        [
            ["INDIVIDUAL", "A", "1742 GOLF RIDGE DR S", "48302"],
            ["INDIVIDUAL", "A", "1742 S GOLF RIDGE DR", "48302"],
            ["INDIVIDUAL", "B", "9 FAR RD", "10001"],
        ],
        ["entity_type", "donor_key", "contributor_street_1", "contributor_zip"],
    )
    r = qs.scan_address_order_variants(df)
    assert r["groups"] == 1


def test_junk_occupations_use_shape_and_donor_history():
    rows = []
    for occupation in ["DOCTOR"] * 9 + ["FDE", "ZZZ"]:
        category = "MEDICAL / HEALTHCARE" if occupation == "DOCTOR" else "OTHER"
        rows.append(["INDIVIDUAL", "d1", occupation, category])

    df = _df(
        rows,
        [
            "entity_type",
            "donor_key",
            "contributor_occupation",
            "occupation_category",
        ],
    )
    report = qs.scan_junk_occupations(df)
    examples = {item["value"]: item for item in report["examples"]}

    assert examples["FDE"]["why"] == "donor-history"
    assert examples["ZZZ"]["why"] == "shape+donor-history"
