from fec.resolve.cache import dedup_by_resolved_address
import pandas as pd
from fec.resolve.rows import ResolveContext, _resolve_retired
from fec.resolve import fec as fec_search
import pytest
from fec.resolve import pipeline as resolve_cli
from fec.resolve.search import AIQuotaExhausted


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


class Response:
    def __init__(self, results, last_indexes=None, status_code=200):
        self.status_code = status_code
        self._body = {"results": results, "pagination": {"last_indexes": last_indexes}}

    def json(self):
        return self._body


def _filing(employer):
    return {"contributor_name": "DOE, JANE", "contributor_state": "NY", "contributor_zip": "10001",
            "contributor_street_1": "10 OAK ST", "contributor_street_2": "APT 2", "sub_id": "OLD-1",
            "two_year_transaction_period": 2026, "committee_id": "C00123456",
            "contributor_employer": employer, "contributor_occupation": "RETIRED" if employer == "RETIRED" else "CEO",
            "contribution_receipt_date": "2025-01-01"}


PERSON = {"prev_key": "donor:D1:2026", "name": "DOE, JANE", "state": "NY", "city": "NEW YORK", "zip": "10001",
          "street_1": "10 OAK ST", "street_2": "APT 2", "latest_date": "2025-01-01",
          "cycle": 2026, "committee_ids": {"C00797670"}}


def test_the_employer_on_the_second_page_is_found(monkeypatch):
    monkeypatch.setattr(fec_search, "FEC_PAGE_SIZE", 2)
    pages = [
        Response([_filing("RETIRED"),
                  {**_filing("FUTURE COMPANY"), "contribution_receipt_date": "2025-02-01"}],
                 {"last_index": "9"}),
        Response([_filing("ACME INDUSTRIES")]),
    ]
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        return pages[len(calls) - 1]

    key, entry = fec_search._fetch_fec_previous_employer(PERSON, "k", get)

    assert entry["employer"] == "ACME INDUSTRIES"
    assert entry["source_street_1"] == "10 OAK ST"
    assert entry["source_street_2"] == "APT 2"
    assert entry["source_sub_id"] == "OLD-1"
    assert entry["source_cycle"] == "2026"
    assert entry["source_committee_id"] == "C00123456"
    assert key == "donor:D1:2026"
    assert calls[0]["two_year_transaction_period"] == 2026
    assert calls[0]["min_date"] == "2025-01-01"
    assert calls[0]["max_date"] == "2025-01-01"
    assert calls[1]["last_index"] == "9"


def test_cli_writes_cached_results_before_reporting_partial(tmp_path, monkeypatch):
    csv_path = tmp_path / "contributions_cleaned.csv"
    csv_path.write_text("placeholder\n", encoding="utf-8")
    source = pd.DataFrame(
        {
            "contribution_receipt_amount": [100],
            "contributor_employer": ["CACHED COMPANY"],
        }
    )
    applied = []

    monkeypatch.setattr(resolve_cli, "read_pipeline_csv", lambda _path: source.copy())
    monkeypatch.setattr(resolve_cli, "load_env", lambda: None)
    monkeypatch.setattr(
        resolve_cli, "_compute_donor_totals", lambda _df: pd.DataFrame()
    )
    monkeypatch.setattr(resolve_cli, "load_manual_locations", lambda *args: None)
    monkeypatch.setattr(
        resolve_cli, "load_manual_previous_employers", lambda *args: None
    )
    monkeypatch.setattr(resolve_cli, "step_cross_record", lambda *args: 0)
    monkeypatch.setattr(resolve_cli, "step_fec_api", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        resolve_cli,
        "step_ai_lookup",
        lambda *args, **kwargs: (_ for _ in ()).throw(AIQuotaExhausted("no credits")),
    )
    monkeypatch.setattr(
        resolve_cli, "dedup_by_resolved_address", lambda *args, **kwargs: (0, 0)
    )

    def apply_cached(df, *_caches):
        applied.append(True)
        return df.assign(employer_city="NEW YORK")

    monkeypatch.setattr(resolve_cli, "apply_results", apply_cached)
    monkeypatch.setattr(resolve_cli, "CLEANED_CSV", csv_path)
    monkeypatch.setattr("sys.argv", ["resolve.py", "--apply"])
    monkeypatch.setattr(resolve_cli, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(resolve_cli, "REPORTS_DIR", tmp_path)
    # Column checks have their own tests.
    for name in ("check_input", "check_output"):
        monkeypatch.setattr(resolve_cli, name, lambda *_args: None)

    with pytest.raises(SystemExit) as stopped:
        resolve_cli.main()

    assert stopped.value.code == 2
    assert applied == [True]
    written = pd.read_csv(csv_path)
    assert written["employer_city"].tolist() == ["NEW YORK"]
    assert not csv_path.with_suffix(".csv.tmp").exists()
