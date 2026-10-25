"""Only documented company aliases expand abbreviations."""
import pandas as pd

from fec.cleaning.work.employer_names import apply_employer_synonyms


def _emps(rows):
    df = pd.DataFrame({"entity_type": ["INDIVIDUAL"] * len(rows),
                       "contributor_employer": rows})
    df, n = apply_employer_synonyms(df)
    return list(df["contributor_employer"]), n


def test_generic_abbreviations_are_not_company_identity():
    emps, n = _emps(["FOO HOLDINGS MGMT", "SUTHERLAND CAPITAL MGMT. INC.", "X GRP", "ACME SVCS"])
    assert n == 1  # punctuation only; MGMT is not expanded
    assert emps == ["FOO HOLDINGS MGMT", "SUTHERLAND CAPITAL MGMT INC", "X GRP", "ACME SVCS"]


def test_expands_inv_except_reviewed_separations():
    # EMA's underspecified aliases were not verified, so its filed text is protected.
    emps, n = _emps(["EMA INV MGMT", "SSI INV MGT", "TRIPLE S INV LLP"])
    assert n == 0
    assert "EMA INV MGMT" in emps
    assert "SSI INV MGT" in emps
    assert "TRIPLE S INV LLP" in emps


def test_unreviewed_abbreviation_variants_stay_apart():
    emps, _ = _emps(["FOO CAPITAL MGMT", "FOO CAPITAL MANAGEMENT"])
    assert set(emps) == {"FOO CAPITAL MGMT", "FOO CAPITAL MANAGEMENT"}


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
    df, n = apply_employer_synonyms(df)
    return list(df["contributor_employer"]), n


def test_assoc_is_not_guessed_from_its_position():
    emps, _ = _assoc([("GOLDBERG REALTY ASSOC.", "GOLDBERG REALTY ASSOC"),
                      ("EIGHTEEN ASSOC", "EIGHTEEN ASSOC"),
                      ("RAD ASSOC HLYWD", "RAD ASSOC HLYWD"),
                      ("JBRODSKY & ASSOC", "JBRODSKY & ASSOC")])
    assert emps == ["GOLDBERG REALTY ASSOC", "EIGHTEEN ASSOC", "RAD ASSOC HLYWD", "JBRODSKY AND ASSOC"]


def test_assoc_needs_a_reviewed_company_rule():
    emps, _ = _assoc([("BRAMAN MANAGEMENT ASSOC", "BRAMAN MANAGEMENT ASSOCIATION"),
                      ("ASSOC FOR WOMENS HEALTH CARE", "ASSOC FOR WOMENS HEALTH CARE")])
    assert emps == ["BRAMAN MANAGEMENT ASSOC", "ASSOC FOR WOMENS HEALTH CARE"]
