"""Tests of donor matching: keys, matching phases, holds and the name each donor gets."""

import pandas as pd

from fec.matching.pipeline import identify_donors


def _row(sub_id, name, first, last, city, state, zip5, street, employer="ACME LLP", occupation="ATTORNEY"):
    return {
        "sub_id": sub_id,
        "transaction_id": f"T{sub_id}",
        "two_year_transaction_period": 2024,
        "recipient_committee": "AIPAC",
        "entity_type": "INDIVIDUAL",
        "contributor_name": name,
        "contributor_first_name": first,
        "contributor_last_name": last,
        "contributor_street_1": street,
        "contributor_street_2": None,
        "contributor_city": city,
        "contributor_state": state,
        "contributor_zip": zip5,
        "contributor_employer": employer,
        "contributor_occupation": occupation,
        "occupation_category": "LEGAL",
        "committee_type": None,
        "contribution_receipt_date": "2024-03-05",
        "contribution_receipt_amount": 100.0,
    }


OLD_ROWS = [
    _row("101", "SHTREMBERG, VICTOR", "VICTOR", "SHTREMBERG", "ENCINO", "CA", "91316", "123 MAPLE ST"),
    _row("102", "SHTREMBERG, VICTOR", "VICTOR", "SHTREMBERG", "LOS ANGELES", "CA", "91316", "123 MAPLE ST"),
    _row("103", "NULL, KAREN", "KAREN", "NULL", "WESTPORT", "CT", "06880", "9 ELM RD", employer="YALE"),
]


NEW_ROWS = [
    _row("201", "SHTREMBERG, VICTOR", "VICTOR", "SHTREMBERG", "LOS ANGELES", "CA", "91316", "123 MAPLE ST"),
    _row("202", "NULL, KAREN", "KAREN", "NULL", "WESTPORT", "CT", "06880", "9 ELM RD", employer="YALE"),
]


def _identify(rows):
    return identify_donors(pd.DataFrame(rows))


def _keys(df):
    return dict(zip(df["sub_id"], df["donor_key"]))


def test_same_donor_gets_one_key():
    keys = _keys(_identify(OLD_ROWS + NEW_ROWS))
    assert keys["101"] == keys["102"] == keys["201"]


def test_same_name_retirees_need_real_shared_evidence():
    retired = {"employer": "RETIRED", "occupation": "RETIRED"}
    rows = [
        _row("501", "PORTER, DAVID", "DAVID", "PORTER", "PRAIRIE VILLAGE", "KS", "66208", "PO BOX 8770", **retired),
        _row(
            "502", "PORTER, DAVID", "DAVID", "PORTER", "MARYLAND HEIGHTS", "MO", "63043", "12459 GLENBUSH DR", **retired
        ),
    ]
    for row in rows:
        row["occupation_category"] = "RETIRED"
    keys = _keys(_identify(rows))
    assert keys["501"] != keys["502"]


def test_keys_stay_with_their_filings_across_runs():
    from fec.matching.donor_keys import keep_earlier_keys

    df = pd.DataFrame(
        {
            "sub_id": ["1", "2", "3", "4", "5", "6"],
            "entity_type": ["INDIVIDUAL"] * 6,
            "donor_key": ["new-a", "new-a", "new-a", "new-b", "new-b", "new-c"],
        }
    )
    # 1-3 were one donor, 4-5 another; 6 is new; the old donor split from 3.
    lost = keep_earlier_keys(df, {"1": "old-a", "2": "old-a", "3": "old-x", "4": "old-b", "5": "old-a"})
    assert df["donor_key"].tolist() == ["old-a", "old-a", "old-a", "old-b", "old-b", "new-c"]
    assert lost == ["old-x"]


def test_truncated_first_name_matches_with_an_anchor():
    # TIM / TIMOTHY at one street are one donor; without an anchor they stay apart.
    rows = [
        _row("601", "WULIGER, TIMOTHY", "TIMOTHY", "WULIGER", "SOLON", "OH", "44139", "7 OAK LN"),
        _row("602", "WULIGER, TIM", "TIM", "WULIGER", "SOLON", "OH", "44139", "7 OAK LN"),
        _row("603", "WULIGER, TIM", "TIM", "WULIGER", "AUSTIN", "TX", "78701", "90 ELM ST", employer="DELL"),
    ]
    keys = _keys(_identify(rows))
    assert keys["601"] == keys["602"] != keys["603"]


def test_short_form_of_two_names_matches_neither():
    # MARI beside MARIANNE and MARISOL names nobody in particular.
    rows = [
        _row("611", "HALE, MARIANNE", "MARIANNE", "HALE", "SOLON", "OH", "44139", "7 OAK LN"),
        _row("612", "HALE, MARISOL", "MARISOL", "HALE", "SOLON", "OH", "44139", "7 OAK LN"),
        _row("613", "HALE, MARI", "MARI", "HALE", "SOLON", "OH", "44139", "7 OAK LN"),
    ]
    keys = _keys(_identify(rows))
    assert len({keys["611"], keys["612"], keys["613"]}) == 3


def test_same_words_in_another_order_are_one_name():
    place = ("BAL HARBOUR", "FL", "33154", "10201 COLLINS AVE")
    rows = [
        _row("621", "GAHFI, ALBERT", "ALBERT", "GAHFI", *place),
        _row("622", "GAHFI, MENACHEM ALBERT", "MENACHEM ALBERT", "GAHFI", *place),
        _row("623", "GAHFI, ALBERT MENACHEM", "ALBERT MENACHEM", "GAHFI", *place),
        _row("624", "FARAHI, DAVID-JACQUES", "DAVID-JACQUES", "FARAHI", "DENVER", "CO", "80206", "373 GARFIELD ST"),
        _row("625", "FARAHI, DAVID JACQUES", "DAVID JACQUES", "FARAHI", "DENVER", "CO", "80206", "373 GARFIELD ST"),
    ]
    keys = _keys(_identify(rows))
    assert keys["621"] == keys["622"] == keys["623"]
    assert keys["624"] == keys["625"]


def test_held_filing_takes_no_part_in_matching(monkeypatch):
    # A reviewed hold keeps a filing outside the donor its name and street would join.
    from fec.matching import donor_keys, identity_rules

    for module in (donor_keys, identity_rules):
        monkeypatch.setattr(module, "HELD_FILINGS", {"633": "GOLDSTEIN, RICHARD | unproven"})
    rows = [
        _row("631", "GOLDSTEIN, RICHARD", "RICHARD", "GOLDSTEIN", "BOCA RATON", "FL", "33432", "9 OTHER ST"),
        _row("632", "GOLDSTEIN, RICHARD", "RICHARD", "GOLDSTEIN", "BOCA RATON", "FL", "33432", "9 OTHER ST"),
        _row("633", "GOLDSTEIN, RICHARD", "RICHARD", "GOLDSTEIN", "BOCA RATON", "FL", "33432", "9 OTHER ST"),
    ]
    df = _identify(rows)
    keys_ = _keys(df)
    assert keys_["631"] == keys_["632"] != keys_["633"]
    assert df.set_index("sub_id")["identity_status"].to_dict() == {
        "631": "confirmed",
        "632": "confirmed",
        "633": "held",
    }


def test_a_donor_gets_one_surname_and_the_fullest_given_name():
    from fec.matching.donor_names import donor_names

    def names(filed, rare=frozenset()):
        firsts, lasts = [f for f, _ in filed], [last for _, last in filed]
        surname, given = donor_names(lasts, firsts, rare)
        return surname, sorted({given_name or "" for given_name in given})

    # the full name over the nickname, then a rare slip
    assert names([("MATT", "ENGEL")] * 3 + [("MATTHEW", "ENGEL")]) == ("ENGEL", ["MATTHEW"])
    assert names([("ZIPORA", "ARISON")] * 5 + [("ZIPORAH", "ARISON")], frozenset({"ZIPORAH"})) == ("ARISON", ["ZIPORA"])
    assert names([("STEVE", "FEIN")] * 3 + [("STEVEN", "FEIN")]) == ("FEIN", ["STEVEN"])  # a common name is no slip
    assert names([("ALIZA", "GUREN")] * 3 + [("ALIZA KARNEY", "GUREN"), ("ALIZA", "KARNEY GUREN")]) == (
        "GUREN",
        ["ALIZA KARNEY"],
    )
    assert names([("JILL", "HARMATZ SANDERS")] * 3 + [("JILL", "SANDERS")]) == ("HARMATZ SANDERS", ["JILL"])
    assert names([("RICHARD", "A FREEDMAN")]) == ("FREEDMAN", ["RICHARD A"])  # an initial in the surname box
    assert names([("JOSH KRAFT", "KRAFT"), ("JOSH", "KRAFT")]) == ("KRAFT", ["JOSH"])  # the surname typed twice
    assert names([("EPHRAIM", "WERNICK")] * 3 + [("EPHRAIM (FRY)", "WERNICK"), ("EPHRAIM FRY", "WERNICK")]) == (
        "WERNICK",
        ["EPHRAIM"],
    )
    assert names([("P R", "ZITELMAN")] * 2 + [("P RICHARD", "ZITELMAN")]) == ("ZITELMAN", ["P RICHARD"])
    # another given name under the same donor keeps its own: a spouse on a joint account
    assert names([("MARGERY", "GOTTESMAN")] * 2 + [("ARCHIE", "GOTTESMAN")]) == ("GOTTESMAN", ["ARCHIE", "MARGERY"])
