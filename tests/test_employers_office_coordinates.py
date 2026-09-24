"""An office takes the best cached point of any key it is cached under (audit F7).

The geocode cache holds an office under the text as resolved ('20900 N.E. 30TH AVENUE,
SUITE 203'), its normalised form and, through donor filings, the street without the unit
('20900 NE 30TH AVE'). 30 published offices sat on a ZIP centroid or a town's point
although a street-level point for the same building was already cached, 12 of them paid
Google points.
"""
import json

import pandas as pd

import build_employers

RAW = "20900 N.E. 30th Avenue, Suite 203"
PLACE = "AVENTURA|FL|33180"


def _build(tmp_path, monkeypatch, geocodes):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "contributor_employer": "ADIVUR US HOLDINGS",
        "previous_employer": "", "contributor_state": "FL", "employer_status": "active",
    }]).to_csv(cleaned, index=False)
    cache = {"ADIVUR US HOLDINGS": {
        "employer_address": RAW, "employer_city": "Aventura", "employer_state": "FL",
        "employer_zip": "33180", "method": "ai_openai_search",
    }}
    (tmp_path / "resolve_employer_addr.json").write_text(json.dumps(cache), encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text(json.dumps(geocodes), encoding="utf-8")
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)
    build_employers.build()
    return pd.read_csv(output, dtype=str, keep_default_na=False, na_values=[]).iloc[0]


def _point(lat, lng, source):
    return {"lat": lat, "lng": lng, "source": source, "validated": True, "zip_checked": True}


def test_a_street_point_under_the_donor_form_key_beats_a_zip_centroid(tmp_path, monkeypatch):
    geocodes = {
        f"{RAW.upper()}|{PLACE}": _point(25.9565, -80.1392, "zip_centroid"),
        f"20900 NE 30TH AVE|{PLACE}": _point(25.9696, -80.1440, "google"),
    }

    location = _build(tmp_path, monkeypatch, geocodes)

    assert location["employer_address"] == "20900 NE 30TH AVE STE 203"
    assert (location["employer_latitude"], location["employer_longitude"]) == ("25.9696", "-80.144")


def test_the_text_as_resolved_wins_a_tie(tmp_path, monkeypatch):
    geocodes = {
        f"{RAW.upper()}|{PLACE}": _point(25.9690, -80.1430, "nominatim"),
        f"20900 NE 30TH AVE|{PLACE}": _point(25.9696, -80.1440, "google"),
    }

    location = _build(tmp_path, monkeypatch, geocodes)

    assert location["employer_latitude"] == "25.969"


def test_a_level_the_ranking_does_not_list_counts_as_a_street_point(tmp_path, monkeypatch):
    # a new engine or a hand-checked level is never ranked below a ZIP centroid
    geocodes = {
        f"{RAW.upper()}|{PLACE}": _point(25.9565, -80.1392, "zip_centroid"),
        f"20900 NE 30TH AVE|{PLACE}": _point(25.9696, -80.1440, "reviewed"),
    }

    location = _build(tmp_path, monkeypatch, geocodes)

    assert location["employer_latitude"] == "25.9696"


def test_a_point_its_own_key_rejects_is_never_taken(tmp_path, monkeypatch):
    # a street point in another state is no upgrade, whatever key holds it
    geocodes = {
        f"{RAW.upper()}|{PLACE}": _point(25.9565, -80.1392, "zip_centroid"),
        f"20900 NE 30TH AVE|{PLACE}": _point(40.7, -74.0, "google"),
    }

    location = _build(tmp_path, monkeypatch, geocodes)

    assert location["employer_latitude"] == "25.9565"


def test_a_hand_checked_point_counts_without_a_cache_entry(tmp_path, monkeypatch):
    # the prune step deletes unused cache keys; a reviewed point lives in the code
    monkeypatch.setattr(build_employers, "REVIEWED_POINTS", {
        f"20900 NE 30TH AVE|{PLACE}": (25.97, -80.145, "checked by hand"),
    })
    monkeypatch.setattr("fec.geocoding.pipeline.reviewed_point", lambda key: (
        (25.97, -80.145) if key == f"20900 NE 30TH AVE|{PLACE}" else None))

    location = _build(tmp_path, monkeypatch, {})

    assert (location["employer_latitude"], location["employer_longitude"]) == ("25.97", "-80.145")
