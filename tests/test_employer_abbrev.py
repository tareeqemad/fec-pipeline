"""Employer abbreviation expansion (MGMT -> MANAGEMENT, run after synonyms)."""
import pandas as pd

from fec.cleaning.employer_synonyms import (
    expand_employer_abbreviations,
    expand_employer_associates,
)


def _emps(rows):
    df = pd.DataFrame({"entity_type": ["INDIVIDUAL"] * len(rows),
                       "contributor_employer": rows})
    df, n = expand_employer_abbreviations(df)
    return list(df["contributor_employer"]), n


def test_expands_mgmt_grp_svcs():
    emps, n = _emps(["FOO HOLDINGS MGMT", "SUTHERLAND CAPITAL MGMT. INC.", "X GRP", "ACME SVCS"])
    assert n == 4
    assert "FOO HOLDINGS MANAGEMENT" in emps
    assert "SUTHERLAND CAPITAL MANAGEMENT INC" in emps   # MGMT expanded, trailing '.' trimmed
    assert "X GROUP" in emps
    assert "ACME SERVICES" in emps


def test_expands_inv_to_investment():
    # verified by hand: every INV employer in this dataset is an Investment firm
    emps, n = _emps(["EMA INV MGMT", "SSI INV MGT", "TRIPLE S INV LLP"])
    assert n == 3
    assert "EMA INVESTMENT MANAGEMENT" in emps
    assert "SSI INVESTMENT MANAGEMENT" in emps
    assert "TRIPLE S INVESTMENT LLP" in emps


def test_collapses_abbrev_variants_to_one():
    emps, _ = _emps(["FOO CAPITAL MGMT", "FOO CAPITAL MANAGEMENT"])
    assert set(emps) == {"FOO CAPITAL MANAGEMENT"}        # the two unify


def test_leaves_substrings_and_ambiguous_untouched():
    # MGMTX is not a whole word; INV is intentionally NOT in the map (ambiguous)
    emps, n = _emps(["MGMTX CORP", "INVESTORS BANK", "MANAGEMENTING CO"])
    assert n == 0
    assert emps == ["MGMTX CORP", "INVESTORS BANK", "MANAGEMENTING CO"]


def test_skips_status_employers():
    emps, n = _emps(["RETIRED", "SELF-EMPLOYED"])
    assert n == 0


# ASSOC is contextual: ASSOCIATES vs ASSOCIATION
def _assoc(pairs):
    df = pd.DataFrame({"entity_type": ["INDIVIDUAL"] * len(pairs),
                       "contributor_employer": [p[0] for p in pairs],
                       "contributor_employer_original": [p[1] for p in pairs]})
    df, n = expand_employer_associates(df)
    return list(df["contributor_employer"]), n


def test_assoc_expands_to_associates_including_midstring():
    emps, _ = _assoc([("GOLDBERG REALTY ASSOC.", "GOLDBERG REALTY ASSOC"),
                      ("EIGHTEEN ASSOC", "EIGHTEEN ASSOC"),
                      ("RAD ASSOC HLYWD", "RAD ASSOC HLYWD"),
                      ("JBRODSKY & ASSOC", "JBRODSKY & ASSOC")])
    assert "GOLDBERG REALTY ASSOCIATES" in emps
    assert "EIGHTEEN ASSOCIATES" in emps
    assert "RAD ASSOCIATES HLYWD" in emps          # mid-string handled
    assert "JBRODSKY & ASSOCIATES" in emps


def test_assoc_keeps_real_associations():
    # association detected from the original ("...ASSOCIATION") or an "ASSOC FOR" opener
    emps, _ = _assoc([("BRAMAN MANAGEMENT ASSOC", "BRAMAN MANAGEMENT ASSOCIATION"),
                      ("ASSOC FOR WOMENS HEALTH CARE", "ASSOC FOR WOMENS HEALTH CARE")])
    assert "BRAMAN MANAGEMENT ASSOCIATION" in emps
    assert "ASSOCIATION FOR WOMENS HEALTH CARE" in emps
