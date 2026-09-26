"""A retired filing never takes a previous employer the donor only filed later (MARGOLIN, RUTH)."""
import pandas as pd

from fec.resolve.pipeline.apply import ResolveContext, _first_filed_employers, _resolve_retired

CLINIC = "ASSOCIATES IN GASTROENTEROLOGY OF UNION COUNTY"


def _retired(date):
    return pd.Series({"donor_key": "ruth", "contribution_receipt_date": date,
                      "contributor_name": "MARGOLIN, RUTH", "contributor_employer": "RETIRED"})


def _context(entry, first_filed=None):
    return ResolveContext(previous_cache={"donor:ruth": entry}, address_lookup={},
                          first_filed=first_filed or {})


def test_an_entry_sourced_after_the_filing_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous", "source_date": "2026-05-03"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_an_undated_entry_for_an_employer_first_filed_later_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous"}
    first = {("ruth", CLINIC): pd.Timestamp("2024-09-02")}
    assert _resolve_retired(_retired("2022-09-28"), _context(entry, first), "NJ", "07000")["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry, first), "NJ", "07000")["previous_employer"] == CLINIC


def test_an_undated_entry_the_donor_never_filed_still_applies():
    entry = {"employer": CLINIC, "method": "cleaned_previous"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_a_hand_set_entry_always_applies():
    entry = {"employer": CLINIC, "method": "manual_override", "source_date": "2026-05-03"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_first_filed_employers_keeps_each_donors_earliest_date():
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * 3,
        "donor_key": ["ruth"] * 3,
        "contributor_employer": [f"{CLINIC} PA", f"{CLINIC} PA", "RETIRED"],
        "contribution_receipt_date": ["2025-01-01", "2024-09-02", "2022-02-02"],
    })
    assert _first_filed_employers(df)[("ruth", f"{CLINIC} PA")] == pd.Timestamp("2024-09-02")
