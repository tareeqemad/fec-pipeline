from fec.resolve.cache import dedup_by_resolved_address
import pandas as pd
from fec.resolve.rows import ResolveContext, _resolve_retired


class _Cache:
    def __init__(self, data=None):
        self.data = data or {}
        self.saves = 0

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1

    def __len__(self):
        return len(self.data)


def _address(city):
    return {
        "employer_address": "1 MAIN ST",
        "employer_city": city,
        "employer_state": "NY",
        "employer_zip": "10001",
        "method": "ai_openai_search",
        "confidence": "HIGH",
    }


def test_address_aliases_are_idempotent():
    cache = _Cache({"IBM": _address("NEW YORK"), "IBM CORP": _address("NEW YORK")})

    assert dedup_by_resolved_address(cache) == (1, 1)
    snapshot = {key: value.copy() for key, value in cache.data.items()}
    assert dedup_by_resolved_address(cache) == (0, 0)

    assert cache.data == snapshot
    assert cache.saves == 1


CLINIC = "ASSOCIATES IN GASTROENTEROLOGY OF UNION COUNTY"


def _retired(date):
    return pd.Series({"donor_key": "ruth", "contribution_receipt_date": date,
                      "contributor_name": "MARGOLIN, RUTH", "contributor_employer": "RETIRED"})


def _context(entry, first_filed=None):
    return ResolveContext(previous_cache={"donor:ruth": entry}, address_lookup={},
                          first_filed=first_filed or {})


def test_an_entry_sourced_after_the_filing_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous", "source_date": "2026-05-03T00:00:00+00:00"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC
