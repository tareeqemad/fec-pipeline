"""Employer locations are stored and selected through one model."""

import pandas as pd
import pytest

import fec.database.loader.employers as employer_loader
from fec.resolve.pipeline.apply import ResolveContext, apply_results, _resolve_row
from fec.resolve.pipeline.manual_overrides import (
    load_manual_locations,
    load_manual_previous_employers,
)
from fec.resolve.pipeline.steps.previous_employer import _cache_entry, step_cross_record
from fec.database.loader.employers import (
    _employment_address_id,
    _latest_employment_rows,
    _make_employer_resolver,
    _previous_employer_id,
    load_employers,
)
from fec.cleaning.quality import run_quality_gates
from fec.cleaning.previous_employer import current_employer_name


class FakeCache(dict):
    def put(self, key, value):
        self[key] = value

    def save(self):
        pass

    def discard(self, key):
        self.pop(key, None)


PRIMARY = {
    "employer_address": "1585 Broadway",
    "employer_city": "New York",
    "employer_state": "NY",
    "employer_zip": "10036",
    "method": "manual_override",
    "confidence": "HIGH",
}
SF_OFFICE = {
    "employer_address": "555 California St",
    "employer_city": "San Francisco",
    "employer_state": "CA",
    "employer_zip": "94104",
    "method": "manual_override",
    "confidence": "HIGH",
}


def _row(employer="BIG FIRM", state="CA", zip_code="94104"):
    return pd.Series({
        "entity_type": "INDIVIDUAL",
        "donor_key": "D1",
        "contributor_employer": employer,
        "contributor_state": state,
        "contributor_zip": zip_code,
        "contributor_name": "DOE, JANE",
        "contributor_street_1": "1 HOME ST",
        "contributor_city": "SOMEWHERE",
    })


def _locations():
    return FakeCache({"BIG FIRM": {**PRIMARY, "locations": [SF_OFFICE]}})


def _resolve(row):
    context = ResolveContext(
        previous_cache=FakeCache(),
        address_lookup=_locations(),
    )
    return _resolve_row(row, context)


def test_curated_previous_employer_is_protected(tmp_path):
    path = tmp_path / "manual_employer_overrides.csv"
    path.write_text(
        "sub_id,previous_employer\n1,SELF-EMPLOYED\n",
        encoding="utf-8",
    )
    df = pd.DataFrame([{
        "sub_id": "1",
        "donor_key": "D1",
        "contributor_name": "KATZ, OZZIE",
        "contributor_state": "AZ",
        "previous_employer": "SELF-EMPLOYED",
    }])
    cache = FakeCache({
        "donor:D1": {"employer": "AZRYEL KATZ", "method": "fec_api"},
    })

    assert load_manual_previous_employers(path, df, cache) == (0, 1)
    assert cache["donor:D1"]["employer"] == "SELF-EMPLOYED"
    assert cache["donor:D1"]["method"] == "manual_override"


def test_curated_previous_employer_can_be_cleared(tmp_path):
    path = tmp_path / "manual_employer_overrides.csv"
    path.write_text(
        "sub_id,previous_employer\n1,[CLEAR]\n",
        encoding="utf-8",
    )
    df = pd.DataFrame([{
        "sub_id": "1",
        "donor_key": "D1",
        "contributor_state": "CA",
        "previous_employer": "WRONG COMPANY",
    }])
    cache = FakeCache({
        "donor:D1": {"employer": "WRONG COMPANY", "method": "fec_api"},
    })

    assert load_manual_previous_employers(path, df, cache) == (0, 1)
    assert cache["donor:D1"] == {
        "employer": "",
        "state": "CA",
        "method": "manual_clear",
    }


def test_same_state_office_wins():
    result = _resolve(_row())

    assert result["employer_address"] == "555 California St"
    assert result["employer_state"] == "CA"
    assert result["resolve_method"] == "nearest_manual_override"


def test_primary_wins_when_it_is_closest():
    row = _row(state="NY", zip_code="10036")
    result = _resolve(row)

    assert result["employer_address"] == "1585 Broadway"
    assert result["resolve_method"] == "manual_override"


def test_primary_is_the_fallback_without_a_matching_zip():
    row = _row(state="TX", zip_code="")
    result = _resolve(row)

    assert result["employer_address"] == "1585 Broadway"


def test_out_of_state_office_does_not_replace_primary():
    row = _row(state="NV", zip_code="89101")
    result = _resolve(row)

    assert result["employer_address"] == "1585 Broadway"
    assert result["resolve_method"] == "manual_override"


def test_legal_suffix_alias_keeps_all_locations():
    df = pd.DataFrame([_row(employer="BIG FIRM PC")])
    cache = FakeCache({"BIG FIRM, P.C.": {**PRIMARY, "locations": [SF_OFFICE]}})

    result = apply_results(df, FakeCache(), cache)

    assert result.loc[0, "employer_address"] == "555 California St"


def test_manual_alias_beats_an_old_exact_ai_address():
    employer = "GOLDENBERG HELLER & ANTOGNOLI PC"
    row = _row(employer=employer, state="IL", zip_code="62025")
    cache = FakeCache({
        employer: {
            "employer_address": "800 Delaware Avenue",
            "employer_city": "Wilmington",
            "employer_state": "DE",
            "employer_zip": "19801",
            "method": "ai_openai",
        },
        "GOLDENBERG HELLER & ANTOGNOLI": {
            "employer_address": "2227 S State Route 157",
            "employer_city": "Edwardsville",
            "employer_state": "IL",
            "employer_zip": "62025",
            "method": "manual_override",
        },
    })

    result = apply_results(pd.DataFrame([row]), FakeCache(), cache)

    assert result.loc[0, "employer_state"] == "IL"
    assert result.loc[0, "employer_address"] == "2227 S State Route 157"


def test_legacy_ai_address_is_not_applied():
    cache = FakeCache({"BIG FIRM": {
        **PRIMARY,
        "method": "ai_openai",
    }})

    result = apply_results(pd.DataFrame([_row()]), FakeCache(), cache)

    assert result.loc[0, "employer_address"] == ""
    assert result.loc[0, "resolve_method"] == "pending"


def test_grounded_search_address_is_applied():
    cache = FakeCache({"BIG FIRM": {
        **PRIMARY,
        "method": "ai_openai_search",
    }})

    result = apply_results(
        pd.DataFrame([_row(state="NY", zip_code="10036")]),
        FakeCache(),
        cache,
    )

    assert result.loc[0, "employer_address"] == "1585 Broadway"
    assert result.loc[0, "resolve_method"] == "ai_openai_search"


def test_resolve_never_rewrites_employer_names():
    rows = pd.DataFrame([
        _row("BIG FIRM"),
        _row("SELF-EMPLOYED"),
        _row("NOT EMPLOYED"),
    ])
    original = rows["contributor_employer"].copy()

    result = apply_results(rows, FakeCache(), _locations())

    pd.testing.assert_series_equal(result["contributor_employer"], original)


def test_committee_keeps_only_its_contributor_address():
    row = _row("")
    row["entity_type"] = "COMMITTEE/PAC"

    result = apply_results(pd.DataFrame([row]), FakeCache(), FakeCache())

    assert result.loc[0, "contributor_street_1"] == "1 HOME ST"
    assert result.loc[0, "employer_status"] == "committee"
    assert pd.isna(result.loc[0, "employer_address"])
    assert result.loc[0, "resolve_method"] == "skip"


def test_student_school_is_not_a_workplace():
    row = _row("NYU")
    row["contributor_occupation"] = "STUDENT"
    row["occupation_category"] = "STUDENT"

    result = _resolve(row)

    assert result["employer_status"] == "not_employed"
    assert result["employer_address"] == ""


def test_self_employed_company_uses_donor_address():
    row = _row("BIG FIRM")
    row["contributor_occupation"] = "SELF-EMPLOYED"
    row["occupation_category"] = "SELF-EMPLOYED"

    result = _resolve(row)

    assert result["employer_status"] == "self_employed"
    assert result["employer_address"] == "1 HOME ST"


def test_not_employed_company_is_kept_but_not_linked():
    row = _row("BIG FIRM")
    row["contributor_occupation"] = "NOT EMPLOYED"
    row["occupation_category"] = "NOT EMPLOYED"

    result = _resolve(row)

    assert result["employer_status"] == "not_employed"
    assert result["employer_address"] == ""
    assert current_employer_name("not_employed", "BIG FIRM") == ""
    assert current_employer_name("self_employed", "BIG FIRM") == "BIG FIRM"


def test_loader_creates_every_referenced_previous_employer(monkeypatch):
    inserted = []

    class Connection:
        def commit(self):
            pass

    class Cursor:
        def execute(self, query):
            pass

        def fetchall(self):
            return [(index, name) for index, (name,) in enumerate(inserted, 1)]

    def capture_values(_cur, _query, rows, **_kwargs):
        inserted.extend(rows)

    monkeypatch.setattr(employer_loader, "execute_values", capture_values)
    monkeypatch.setattr(employer_loader, "_count", lambda *_args: len(inserted))

    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL",
            "employer_status": "active",
            "contributor_employer": "CURRENT CO",
            "previous_employer": "",
        },
        {
            "entity_type": "INDIVIDUAL",
            "employer_status": "retired",
            "contributor_employer": "RETIRED",
            "previous_employer": "OLDER CO",
        },
        {
            "entity_type": "INDIVIDUAL",
            "employer_status": "retired",
            "contributor_employer": "RETIRED",
            "previous_employer": "NEWER CO",
        },
        {
            "entity_type": "ORGANIZATION",
            "employer_status": "organization",
            "contributor_employer": "WRONG ORG EMPLOYER",
            "previous_employer": "",
        },
    ])

    result = load_employers(Connection(), Cursor(), rows)

    assert set(result) == {"CURRENT CO", "OLDER CO", "NEWER CO"}


def test_resolve_reports_a_clean_owned_employer_contradiction():
    row = _row("BIG FIRM")
    row["contributor_occupation"] = "RETIRED"
    row["occupation_category"] = "RETIRED"
    result = apply_results(
        pd.DataFrame([row]),
        FakeCache(),
        _locations(),
    )

    assert result.loc[0, "contributor_employer"] == "BIG FIRM"
    assert result.loc[0, "employer_status"] == "active"
    assert not run_quality_gates(result)["checks"]["retired_active_sync"]["passed"]


def test_self_employed_uses_reported_donor_address():
    row = _row("SELF-EMPLOYED")
    row["employer_status"] = "self_employed"
    address_ids = {
        ("1 HOME ST", "", "SOMEWHERE", "CA", "94104"): 17,
    }

    result = _employment_address_id(row, None, {}, address_ids)

    assert result == 17


def test_self_employed_keeps_a_city_level_reported_address():
    row = _row("SELF-EMPLOYED")
    row["employer_status"] = "self_employed"
    row["contributor_street_1"] = ""
    address_ids = {
        ("", "", "SOMEWHERE", "CA", "94104"): 18,
    }

    result = _employment_address_id(row, None, {}, address_ids)

    assert result == 18


def test_missing_self_employed_address_fails_loudly():
    row = _row("SELF-EMPLOYED")
    row["donor_key"] = "donor-1"
    row["employer_status"] = "self_employed"

    with pytest.raises(RuntimeError, match="donor-1"):
        _employment_address_id(row, None, {}, {})


def test_employment_uses_one_complete_latest_filing():
    rows = pd.DataFrame([
        {
            "donor_key": "donor-1",
            "contributor_employer": "SELF-EMPLOYED",
            "contributor_occupation": "CONSULTANT",
            "contribution_receipt_date": "2025-01-01",
            "contributor_street_1": "",
            "contributor_city": "LATEST CITY",
        },
        {
            "donor_key": "donor-1",
            "contributor_employer": "SELF-EMPLOYED",
            "contributor_occupation": "CONSULTANT",
            "contribution_receipt_date": "2024-01-01",
            "contributor_street_1": "1 OLD ST",
            "contributor_city": "OLD CITY",
        },
    ])

    result = _latest_employment_rows(rows).iloc[0]

    assert result["contributor_street_1"] == ""
    assert result["contributor_city"] == "LATEST CITY"


def test_not_employed_never_gets_a_work_address():
    row = _row("NOT EMPLOYED")
    row["employer_status"] = "not_employed"

    result = _employment_address_id(
        row,
        "BIG FIRM",
        _locations(),
        {("1585 Broadway", "", "New York", "NY", "10036"): 21},
    )

    assert result is None


def test_previous_employer_belongs_only_to_retired_rows():
    employer_ids = {"donor-1": 42}

    assert _previous_employer_id("retired", "donor-1", employer_ids) == 42
    assert _previous_employer_id("not_employed", "donor-1", employer_ids) is None
    assert _previous_employer_id("self_employed", "donor-1", employer_ids) is None
    assert _previous_employer_id("active", "donor-1", employer_ids) is None


def test_student_school_is_removed_from_previous_employer_cache():
    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL", "donor_key": "D1",
            "contributor_name": "STUDENT, JANE", "contributor_state": "NY",
            "contributor_employer": "NYU", "contributor_occupation": "STUDENT",
            "occupation_category": "STUDENT", "contribution_receipt_date": "2022-01-01",
        },
        {
            "entity_type": "INDIVIDUAL", "donor_key": "D1",
            "contributor_name": "STUDENT, JANE", "contributor_state": "NY",
            "contributor_employer": "RETIRED", "contributor_occupation": "RETIRED",
            "occupation_category": "RETIRED", "contribution_receipt_date": "2024-01-01",
        },
    ])
    cache = FakeCache({
        "donor:D1": {"employer": "NYU", "method": "cross_record"},
    })

    step_cross_record(rows, cache)

    assert "donor:D1" not in cache


def test_stale_cleaned_previous_employer_is_removed():
    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL", "donor_key": "D1",
            "contributor_name": "OVES, LYNN", "contributor_state": "GA",
            "contributor_employer": "SELF-EMPLOYED",
            "contributor_occupation": "ADVOCATE",
            "occupation_category": "OTHER", "previous_employer": "",
            "contribution_receipt_date": "2024-01-01",
        },
        {
            "entity_type": "INDIVIDUAL", "donor_key": "D1",
            "contributor_name": "OVES, LYNN", "contributor_state": "GA",
            "contributor_employer": "RETIRED",
            "contributor_occupation": "RETIRED",
            "occupation_category": "RETIRED", "previous_employer": "",
            "contribution_receipt_date": "2025-01-01",
        },
    ])
    cache = FakeCache({
        "donor:D1": {"employer": "ADVOCATE", "method": "cleaned_previous"},
    })

    step_cross_record(rows, cache)

    assert "donor:D1" not in cache


def test_fec_cache_rejects_a_bare_donor_name():
    entry = _cache_entry(
        "ALISA ABECASSIS",
        state="FL",
        method="fec_api",
        source_name="ABECASSIS, ALISA",
    )

    assert entry == {"employer": "", "method": "fec_api_not_found"}


def test_fec_cache_keeps_an_own_named_legal_company():
    entry = _cache_entry(
        "JOEL REINSTEIN, PLLC",
        state="NY",
        method="fec_api",
        source_name="REINSTEIN, JOEL",
    )

    assert entry["employer"] == "JOEL REINSTEIN PLLC"


def test_fec_cache_rejects_job_titles_as_employers():
    for title in ("ATTORNEY", "PRESIDENT CEO", "INVESTMENT ADVISOR"):
        entry = _cache_entry(
            title,
            state="NY",
            method="fec_api",
            source_name="DOE, JANE",
        )

        assert entry == {"employer": "", "method": "fec_api_not_found"}


def test_fec_cache_keeps_explicit_self_employment():
    for value in ("SELF", "SELF EMPLOYED", "SELF-EMPLOYED", "SELF: CONSULTING"):
        entry = _cache_entry(
            value,
            state="NY",
            method="fec_api",
            source_name="DOE, JANE",
        )

        assert entry["employer"] == "SELF-EMPLOYED"


def test_cleaned_previous_employer_refreshes_a_failed_cache_entry():
    rows = pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "donor_key": "D1",
        "contributor_name": "RETIRED, JANE", "contributor_state": "NY",
        "contributor_employer": "RETIRED", "contributor_occupation": "RETIRED",
        "occupation_category": "RETIRED", "previous_employer": "ACME INC",
        "contribution_receipt_date": "2024-01-01",
    }])
    cache = FakeCache({
        "donor:D1": {"employer": "", "method": "fec_api_not_found"},
    })

    step_cross_record(rows, cache)

    assert cache["donor:D1"]["employer"] == "ACME"
    assert cache["donor:D1"]["method"] == "cleaned_previous"


def test_same_previous_company_keeps_richer_cache_entry():
    rows = pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "donor_key": "D1",
        "contributor_name": "RETIRED, JANE", "contributor_state": "NY",
        "contributor_employer": "RETIRED", "contributor_occupation": "RETIRED",
        "occupation_category": "RETIRED", "previous_employer": "ACME",
        "contribution_receipt_date": "2024-01-01",
    }])
    original = {
        "employer": "ACME INC",
        "employer_normalized": "ACME INC",
        "employer_source": "ACME, INC.",
        "method": "fec_api",
        "source_date": "2020-01-01",
    }
    cache = FakeCache({"donor:D1": original.copy()})

    step_cross_record(rows, cache)

    assert cache["donor:D1"] == original


def test_loader_does_not_guess_an_employer_alias():
    resolve = _make_employer_resolver({"ACME, INC.": 7})

    assert resolve("ACME, INC.") == 7
    assert resolve("ACME INC") is None


def test_manual_csv_builds_one_location_entry(tmp_path):
    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "BIG FIRM,1585 Broadway,New York,NY,10036,true,Primary\n"
        "BIG FIRM,555 California St,San Francisco,CA,94104,false,Office\n",
        encoding="utf-8",
    )
    cache = FakeCache()

    load_manual_locations(path, cache)

    assert len(cache) == 1
    assert cache["BIG FIRM"]["employer_city"] == "New York"
    assert cache["BIG FIRM"]["locations"][0]["employer_city"] == "San Francisco"


def test_manual_location_keeps_its_evidence(tmp_path):
    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note,source_name,source_url\n"
        "ACCESS FUND,44 Montgomery St,San Francisco,CA,94104,true,"
        "HIGH (VERIFIED),SEC Form D,https://www.sec.gov/example\n",
        encoding="utf-8",
    )
    cache = FakeCache()

    load_manual_locations(path, cache)

    assert cache["ACCESS FUND"]["source_name"] == "SEC Form D"
    assert cache["ACCESS FUND"]["source_url"] == "https://www.sec.gov/example"


def test_uncertain_manual_address_stays_in_review(tmp_path):
    from fec.resolve.pipeline.steps.ai_employer import _needs_ai

    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "BIG FIRM,1 Main St,New York,NY,10001,true,MEDIUM - verify\n",
        encoding="utf-8",
    )
    cache = FakeCache()

    load_manual_locations(path, cache)

    assert cache["BIG FIRM"]["method"] == "manual_review"
    assert cache["BIG FIRM"]["confidence"] == "LOW"
    assert _needs_ai(cache["BIG FIRM"], "current-resolver")


def test_grounded_result_beats_uncertain_manual_address(tmp_path):
    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "BIG FIRM,1 Guess St,Albany,NY,12207,true,Likely address\n",
        encoding="utf-8",
    )
    cache = FakeCache({"BIG FIRM": {
        **PRIMARY,
        "method": "ai_openai_search",
    }})

    load_manual_locations(path, cache)

    assert cache["BIG FIRM"]["employer_address"] == PRIMARY["employer_address"]
    assert cache["BIG FIRM"]["method"] == "ai_openai_search"


def test_manual_locations_replace_stale_manual_entries(tmp_path):
    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "BIG FIRM,168 N Meramec Ave,St. Louis,MO,63105,false,Office\n",
        encoding="utf-8",
    )
    cache = FakeCache({"BIG FIRM": {
        **PRIMARY,
        "locations": [
            {**SF_OFFICE, "method": "manual_override"},
            {**SF_OFFICE, "method": "ai_openai_search"},
        ],
    }})

    load_manual_locations(path, cache)

    locations = cache["BIG FIRM"]["locations"]
    assert len(locations) == 2
    assert locations[0]["method"] == "ai_openai_search"
    assert locations[1]["employer_city"] == "Saint Louis"


def test_deleted_manual_locations_leave_the_cache(tmp_path):
    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "OTHER FIRM,1 Main St,Boston,MA,02108,true,Verified\n",
        encoding="utf-8",
    )
    cache = FakeCache({
        "OLD FIRM": {**PRIMARY, "method": "manual_override"},
        "AI FIRM": {
            **PRIMARY,
            "method": "ai_openai_search",
            "locations": [{**SF_OFFICE, "method": "manual_override"}],
        },
    })

    load_manual_locations(path, cache)

    assert "OLD FIRM" not in cache
    assert "locations" not in cache["AI FIRM"]
    assert cache["AI FIRM"]["method"] == "ai_openai_search"


def test_invalid_manual_address_stays_blank(tmp_path):
    from fec.resolve.pipeline.steps.ai_employer import _needs_ai

    path = tmp_path / "manual_employer_addresses.csv"
    path.write_text(
        "name,address,city,state,zip,is_primary,note\n"
        "LINE G,,,,,true,INVALID: residential address\n",
        encoding="utf-8",
    )
    cache = FakeCache({"LINE G": {"employer_address": "123 HOME ST"}})

    load_manual_locations(path, cache)

    assert cache["LINE G"]["employer_address"] == ""
    assert cache["LINE G"]["method"] == "manual_invalid"
    assert not _needs_ai(cache["LINE G"], "current-resolver")


def test_explicit_not_found_is_not_replaced_by_an_alias():
    df = pd.DataFrame([_row("CRESSON MANAGEMENT VIRGIN ISLANDS LLC", "VI", "00802")])
    cache = FakeCache({
        "CRESSON MANAGEMENT VIRGIN ISLANDS LLC": {
            "employer_address": "",
            "method": "ai_not_found",
        },
        "CRESSON MANAGEMENT VIRGIN ISLANDS, LLC": PRIMARY,
    })

    result = apply_results(df, FakeCache(), cache)

    assert result.loc[0, "employer_address"] == ""
    assert result.loc[0, "resolve_method"] == "pending"
