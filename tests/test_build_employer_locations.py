"""The employer builder emits one unified location file."""

import json

import pandas as pd
import pytest

import build_employers


def test_build_rejects_unfinished_resolve_output(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    pd.DataFrame({
        "resolve_method": ["pending"],
    }).to_csv(cleaned, index=False)
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)

    with pytest.raises(ValueError, match="geocode.py --employer-only"):
        build_employers.build()


def test_builds_primary_and_additional_locations(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL",
            "contributor_employer": "BIG FIRM",
            "previous_employer": "",
            "contributor_state": "NY",
            "employer_status": "active",
            "employer_address": "1585 Broadway",
            "employer_city": "New York",
            "employer_state": "NY",
            "employer_zip": "10036",
            "employer_latitude": "40.76",
            "employer_longitude": "-73.98",
        },
        {
            "entity_type": "INDIVIDUAL",
            "contributor_employer": "BIG FIRM",
            "previous_employer": "",
            "contributor_state": "CA",
            "employer_status": "active",
            "employer_address": "555 California St",
            "employer_city": "San Francisco",
            "employer_state": "CA",
            "employer_zip": "94104",
            "employer_latitude": "37.79",
            "employer_longitude": "-122.40",
        },
    ]).to_csv(cleaned, index=False)
    cache = {
        "BIG FIRM": {
            "employer_address": "1585 Broadway",
            "employer_city": "New York",
            "employer_state": "NY",
            "employer_zip": "10036",
            "method": "manual_override",
            "locations": [{
                "employer_address": "555 California St",
                "employer_city": "San Francisco",
                "employer_state": "CA",
                "employer_zip": "94104",
                "method": "manual_override",
            }],
        }
    }
    (tmp_path / "resolve_employer_addr.json").write_text(
        json.dumps(cache), encoding="utf-8",
    )
    (tmp_path / "geocode_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()

    locations = pd.read_csv(output, keep_default_na=False)
    assert locations["employer_name"].tolist() == ["BIG FIRM", "BIG FIRM"]
    assert locations["is_primary"].tolist() == [True, False]
    assert set(locations["employer_state"]) == {"NY", "CA"}
    assert set(locations["address_trust"]) == {"verified"}

    slim = pd.read_csv(cleaned)
    assert "employer_address" not in slim.columns
    assert "employer_latitude" not in slim.columns

    build_employers.build()
    rerun = pd.read_csv(output, keep_default_na=False)
    pd.testing.assert_frame_equal(locations, rerun)


def test_build_never_rewrites_cleaned_employer_names(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    names = ["ACME LLC", "ACME, L.L.C."]
    pd.DataFrame({
        "entity_type": ["INDIVIDUAL", "INDIVIDUAL"],
        "contributor_employer": names,
        "previous_employer": ["", ""],
        "contributor_state": ["NY", "NY"],
        "employer_status": ["active", "active"],
    }).to_csv(cleaned, index=False)
    (tmp_path / "resolve_employer_addr.json").write_text("{}", encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()

    result = pd.read_csv(cleaned, keep_default_na=False)
    assert result["contributor_employer"].tolist() == names
    assert set(pd.read_csv(output)["employer_name"]) == set(names)


def test_uncertain_manual_address_is_not_marked_verified():
    source, trust = build_employers._trust(
        "manual_review", "NY", {"NY"},
    )

    assert source == "manual"
    assert trust == "uncorroborated"


def test_build_ignores_company_text_on_not_employed_rows(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame({
        "entity_type": ["INDIVIDUAL"],
        "contributor_employer": ["NYU"],
        "previous_employer": [""],
        "contributor_state": ["NY"],
        "employer_status": ["not_employed"],
    }).to_csv(cleaned, index=False)
    (tmp_path / "resolve_employer_addr.json").write_text("{}", encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()

    assert pd.read_csv(output).empty
    result = pd.read_csv(cleaned, keep_default_na=False)
    assert result.loc[0, "contributor_employer"] == "NYU"


def test_build_withdraws_an_invalidated_address(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "contributor_employer": "ACME",
        "previous_employer": "",
        "contributor_state": "NY",
        "employer_status": "active",
    }]).to_csv(cleaned, index=False)
    pd.DataFrame([{
        "employer_name": "ACME",
        "employer_address": "1 WRONG ST",
        "employer_city": "NEW YORK",
        "employer_state": "NY",
        "employer_zip": "10001",
        "employer_latitude": "40.0",
        "employer_longitude": "-73.0",
        "is_primary": True,
        "address_source": "ai",
        "address_trust": "uncorroborated",
    }]).to_csv(output, index=False)
    cache = {
        "ACME": {
            "employer_address": "",
            "method": "manual_invalid",
        },
    }
    (tmp_path / "resolve_employer_addr.json").write_text(
        json.dumps(cache), encoding="utf-8",
    )
    (tmp_path / "geocode_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()
    first = pd.read_csv(output, keep_default_na=False)
    assert first.loc[0, "employer_name"] == "ACME"
    assert first.loc[0, "employer_address"] == ""

    build_employers.build()
    second = pd.read_csv(output, keep_default_na=False)
    pd.testing.assert_frame_equal(first, second)


def test_manual_canonical_address_beats_old_exact_ai(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    employer = "GOLDENBERG HELLER & ANTOGNOLI PC"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "contributor_employer": employer,
        "previous_employer": "",
        "contributor_state": "IL",
        "employer_status": "active",
    }]).to_csv(cleaned, index=False)
    cache = {
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
            "locations": [{
                "employer_address": "168 N Meramec Ave, Suite 101",
                "employer_city": "Saint Louis",
                "employer_state": "MO",
                "employer_zip": "63105",
                "method": "manual_override",
            }],
        },
    }
    (tmp_path / "resolve_employer_addr.json").write_text(
        json.dumps(cache), encoding="utf-8",
    )
    (tmp_path / "geocode_cache.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()

    result = pd.read_csv(output, keep_default_na=False)
    assert set(result["employer_state"]) == {"IL", "MO"}
    assert "DE" not in set(result["employer_state"])
    assert set(result["address_trust"]) == {"verified"}


def test_build_reports_missing_resolve_columns(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL",
        "contributor_employer": "ACME",
        "previous_employer": "",
        "contributor_state": "NY",
    }]).to_csv(cleaned, index=False)
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)

    with pytest.raises(ValueError, match="resolve.py --apply"):
        build_employers.build()
