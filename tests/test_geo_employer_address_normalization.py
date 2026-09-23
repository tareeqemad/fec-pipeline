"""employer_locations.csv stores US employer addresses in the donor-address form.

The AI / manual lookups returned '1211 Avenue of the Americas' / 'New York' while the
donor rows say '1211 AVE OF THE AMERICAS' / 'NEW YORK', so the loader stored one
building twice (88 case-only duplicate groups in the audited database).
"""
import json

import pandas as pd

import build_employers


def _frame(rows):
    return pd.DataFrame(rows, columns=[
        "employer_name", "employer_address", "employer_city", "employer_state", "employer_zip",
    ])


def test_us_addresses_take_the_donor_form():
    frame = _frame([
        ("FOX NEWS", "1211 Avenue of the Americas", "New York", "NY", "10036"),
        ("THE KRAFT GROUP", "One Patriot Place", "Foxborough", "MA", "02035"),
        ("NATIONAL DISTRIBUTING COMPANY", "1 National Dr SW", "Atlanta", "GA", "30336"),
        ("SOME FIRM", "11610 Ash Street, Suite 200", "St. Louis", "MO", "63101-1234"),
    ])

    result = build_employers.normalize_location_addresses(frame)

    assert result[["employer_address", "employer_city", "employer_zip"]].values.tolist() == [
        ["1211 AVE OF THE AMERICAS", "NEW YORK", "10036"],
        ["ONE PATRIOT PL", "FOXBORO", "02035"],
        ["1 NATIONAL DR SW", "ATLANTA", "30336"],
        ["11610 ASH ST STE 200", "SAINT LOUIS", "63101"],
    ]


def test_editorial_notes_in_parentheses_are_dropped():
    frame = _frame([
        ("FELD ENTERTAINMENT INC", "P.O. Box 1000 (Feld Entertainment HQ / administrative offices)",
         "Palmetto", "FL", "34220"),
        ("WTW", "200 Liberty Street, 6th Floor (Brookfield Place)", "New York", "NY", "10281"),
        ("JORDAN RAMIS PC", "1211 SW 5th Ave, Ste 2700 (PacWest, 27th Floor)", "Portland", "OR", "97204"),
        ("GEORGE WASHINGTON UNIVERSITY", "400 North Capitol Street NW (Hall of the States Building)",
         "Washington", "DC", "20001"),
    ])

    streets = build_employers.normalize_location_addresses(frame)["employer_address"].tolist()

    assert streets == ["PO BOX 1000", "200 LIBERTY ST 6TH FLOOR", "1211 SW 5TH AVE STE 2700",
                       "400 N CAPITOL ST NW"]


def test_nothing_in_front_of_the_suite_is_lost():
    frame = _frame([
        ("ADAPTIVE BIO", "One Kendall Square, Building 600, Suite 380", "Cambridge", "MA", "02139"),
        ("SUN CAPITAL PARTNERS", "666 Third Avenue, Floor 24, Suite 2402", "New York", "NY", "10017"),
    ])

    streets = build_employers.normalize_location_addresses(frame)["employer_address"].tolist()

    assert streets == ["ONE KENDALL SQ BUILDING 600 STE 380", "666 THIRD AVE FLOOR 24 STE 2402"]


def test_foreign_addresses_stay_exactly_as_given():
    frame = _frame([
        ("KIFO", "York Gate, 100 Marylebone Road", "London", "", "NW1 5DX"),
        ("SHOPPERAI", "65 Yigal Alon Street", "Tel Aviv", "", "6744316"),
        ("STANTON SAS", "Km 25 Via a Sibate", "Sibate", "CUNDINAMARCA", ""),
    ])

    result = build_employers.normalize_location_addresses(frame)

    pd.testing.assert_frame_equal(result, frame)


def test_normalising_twice_changes_nothing():
    frame = _frame([
        ("FOX NEWS", "1211 Avenue of the Americas", "New York", "NY", "10036"),
        ("WTW", "200 Liberty Street, 6th Floor (Brookfield Place)", "New York", "NY", "10281"),
        ("CITY", "City Hall", "New York", "NY", "10007"),
        ("", "", "", "", ""),
    ])
    once = build_employers.normalize_location_addresses(frame)
    pd.testing.assert_frame_equal(build_employers.normalize_location_addresses(once), once)


def test_build_shares_one_spelling_and_keeps_the_original_geocode_key(tmp_path, monkeypatch):
    cleaned = tmp_path / "contributions_cleaned.csv"
    output = tmp_path / "employer_locations.csv"
    pd.DataFrame([{
        "entity_type": "INDIVIDUAL", "contributor_employer": name, "previous_employer": "",
        "contributor_state": "CT", "employer_status": "active",
    } for name in ("OCTAGON", "STEWARD PARTNERS")]).to_csv(cleaned, index=False)
    resolve_cache = {
        "OCTAGON": {
            "employer_address": "400 Atlantic Street", "employer_city": "Stamford",
            "employer_state": "CT", "employer_zip": "06901", "method": "ai_search",
            # the same office spelled twice for one employer collapses to one row
            "locations": [{
                "employer_address": "400 ATLANTIC ST", "employer_city": "STAMFORD",
                "employer_state": "CT", "employer_zip": "06901", "method": "ai_openai",
            }],
        },
        "STEWARD PARTNERS": {
            "employer_address": "400 ATLANTIC STREET", "employer_city": "STAMFORD",
            "employer_state": "CT", "employer_zip": "06901", "method": "manual_override",
        },
    }
    # the cache only knows the text as it was resolved
    geocodes = {"400 ATLANTIC STREET|STAMFORD|CT|06901": {
        "lat": 41.0503801, "lng": -73.5388891, "source": "census", "validated": True,
    }}
    (tmp_path / "resolve_employer_addr.json").write_text(json.dumps(resolve_cache), encoding="utf-8")
    (tmp_path / "geocode_cache.json").write_text(json.dumps(geocodes), encoding="utf-8")
    monkeypatch.setattr(build_employers, "CLEANED_CSV", cleaned)
    monkeypatch.setattr(build_employers, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_employers, "EMPLOYER_LOCATIONS_CSV", output)

    build_employers.build()

    locations = pd.read_csv(output, dtype=str, keep_default_na=False)
    assert locations["employer_name"].tolist() == ["OCTAGON", "STEWARD PARTNERS"]
    assert set(locations["employer_address"]) == {"400 ATLANTIC ST"}
    assert set(locations["employer_city"]) == {"STAMFORD"}
    assert locations["employer_latitude"].tolist() == ["41.0503801", "41.0503801"]
    assert locations.loc[0, "address_trust"] == "grounded"   # the better-evidenced duplicate wins


def test_street_and_building_names_ending_in_office_are_not_units():
    # UNIT_EXTRACT reads 'OFFICE <word>' at the end as a unit; the word here is a
    # street type or a building name, not a unit number
    frame = _frame([
        ("ALTER TRADING", "700 Office Parkway", "Saint Louis", "MO", "63141"),
        ("SULLIVAN & WORCESTER LLP", "1 Post Office Square", "Boston", "MA", "02109"),
        ("NOETIC TECHNOLOGIES", "10 Post Office Square", "Boston", "MA", "02109"),
        ("HOUSE JUDICIARY COMMITTEE", "2138 Rayburn House Office Building", "Washington", "DC", "20515"),
        ("NYS SENATE", "State Capitol Building, Legislative Office Building", "Albany", "NY", "12247"),
    ])

    streets = build_employers.normalize_location_addresses(frame)["employer_address"].tolist()

    assert not any(" OFF " in f" {street} " for street in streets), streets
    assert streets[:3] == ["700 OFFICE PKWY", "1 POST OFFICE SQ", "10 POST OFFICE SQ"]
    assert streets[3] == "2138 RAYBURN HOUSE OFFICE BUILDING"


def test_real_unit_codes_are_still_abbreviated():
    abbreviate = build_employers._abbreviate_trailing_unit
    assert abbreviate("1 LETTERMAN DR BUILDING C") == "1 LETTERMAN DR BLDG C"
    assert abbreviate("7030 S YALE AVE SUITE E-100") == "7030 S YALE AVE STE E-100"
    assert abbreviate("848 BRICKELL AVE SUITE 2A") == "848 BRICKELL AVE STE 2A"
    assert abbreviate("11610 ASH ST SUITE 200") == "11610 ASH ST STE 200"
    # a spelled-out unit is left alone rather than guessed at
    assert abbreviate("175 STRAFFORD AVE SUITE ONE") == "175 STRAFFORD AVE SUITE ONE"
