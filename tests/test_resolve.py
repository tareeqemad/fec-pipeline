import pandas as pd
from fec.resolve.rows import ResolveContext, _resolve_retired


CLINIC = "ASSOCIATES IN GASTROENTEROLOGY OF UNION COUNTY"


def _retired(date):
    return pd.Series({"donor_key": "ruth", "contribution_receipt_date": date,
                      "contributor_name": "MARGOLIN, RUTH", "contributor_employer": "RETIRED"})


def _context(entry, first_filed=None):
    return ResolveContext(previous_cache={"donor:ruth": entry}, first_filed=first_filed or {})


def test_an_entry_sourced_after_the_filing_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous", "source_date": "2026-05-03T00:00:00+00:00"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry))["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry))["previous_employer"] == CLINIC
