"""Canonical display names per donor: surname choice, first-name decoration, initials.

Every case is a real filing pattern from data/contributions.csv (audit of
2026-09-23); donor_key never changes here, only the display name.
"""

import pandas as pd

from fec.donor_match.canonicalize import (
    _canonical_person_name,
    canonicalize_donor_names,
)
from fec.donor_match.name_choice import _first_core
from fec.donor_match.canonical_employers import unify_org_donor_suffix_variants

COLS = [
    "entity_type",
    "donor_key",
    "contributor_name",
    "contributor_first_name",
    "contributor_last_name",
    "contribution_receipt_date",
]


def _donor(*filings, key="K"):
    """filings: (last, first, count[, date]) tuples for one donor."""
    rows = []
    for filing in filings:
        last, first, count = filing[:3]
        date = filing[3] if len(filing) > 3 else "2024-01-01"
        name = f"{last}, {first}" if first else last
        rows += [["INDIVIDUAL", key, name, first, last, date]] * count
    return pd.DataFrame(rows, columns=COLS)


def _name(df):
    canonicalize_donor_names(df)
    assert df["contributor_name"].nunique() == 1
    row = df.iloc[0]
    return row["contributor_last_name"], row["contributor_first_name"]


# --- surname -------------------------------------------------------------


def test_joint_filing_in_surname_field_does_not_win():
    """9bf80b2c1d3c: 3x 'DIANE R. TISHKOFF, ROCHEL GROSZ' + 1x 'TISHKOFF, DIANE'."""
    df = _donor(("DIANE R. TISHKOFF", "ROCHEL GROSZ", 3), ("TISHKOFF", "DIANE", 1))
    assert _name(df) == ("TISHKOFF", "DIANE R.")
    assert "ROCHEL" not in df["contributor_name"].iloc[0]


def test_whole_name_in_surname_field_loses_a_tie():
    """9b867a78e882: 'ROSEN, EVAN' + 'EVAN ROSEN, ER' was stored as last 'EVAN ROSEN', first 'ER'."""
    df = _donor(("EVAN ROSEN", "ER", 1, "2026-07-01"), ("ROSEN", "EVAN", 1, "2023-10-24"))
    assert _name(df) == ("ROSEN", "EVAN")


def test_glued_initial_loses_a_tie():
    """3c6d47898a38: 2x 'B.POLLACK' vs 2x 'POLLACK' went to B.POLLACK alphabetically."""
    df = _donor(("B.POLLACK", "ELLIOTT", 2), ("POLLACK", "ELLIOTT", 2))
    assert _name(df) == ("POLLACK", "ELLIOTT")


def test_initial_variant_counts_toward_the_clean_surname():
    """adf595b0e7cf: 9x 'A LEVY' vs 8x 'LEVY'; the donor's own clean spelling wins."""
    df = _donor(("A LEVY", "RICHARD", 9), ("LEVY", "RICHARD", 8))
    assert _name(df) == ("LEVY", "RICHARD")
    df = _donor(("E SCHLOSS", "H STEPHEN", 3), ("SCHLOSS", "H STEPHEN", 1))
    assert _name(df) == ("SCHLOSS", "H STEPHEN")


def test_real_compound_surname_is_not_shortened():
    """HARMATZ is not one of the donor's given names: HARMATZ SANDERS may be a real double surname."""
    df = _donor(("HARMATZ SANDERS", "ANDREA", 3), ("SANDERS", "ANDREA", 1))
    assert _name(df) == ("HARMATZ SANDERS", "ANDREA")


def test_unexplained_surname_tie_keeps_the_previous_choice():
    """KADIS vs KADIIS (1:1) cannot be decided from the data: no new tie rule."""
    df = _donor(("KADIS", "CLAUDIA", 1, "2024-05-01"), ("KADIIS", "CLAUDIA", 1, "2022-01-01"))
    assert _name(df) == ("KADIIS", "CLAUDIA")
    df = _donor(("KACOBS", "STEVEN", 1, "2025-01-01"), ("JACOBS", "STEVEN", 1, "2023-01-01"))
    assert _name(df) == ("JACOBS", "STEVEN")


def test_middle_name_in_surname_field_is_explained_by_own_filing():
    """68e9cb0c5e8c: 4x 'LEITMAN BAILEY, ADAM' + 1x 'BAILEY, ADAM LEITMAN'."""
    df = _donor(("LEITMAN BAILEY", "ADAM", 4), ("BAILEY", "ADAM LEITMAN", 1))
    assert _name(df) == ("BAILEY", "ADAM LEITMAN")


def test_initial_filed_only_in_surname_moves_to_first_name():
    assert _name(_donor(("W. HAHN", "LYNN", 2))) == ("HAHN", "LYNN W.")
    assert _name(_donor(("M JOEL", "RICHARD", 1))) == ("JOEL", "RICHARD M")
    assert _name(_donor(("A PEAKE-MEYERING", "LAURA", 1))) == ("PEAKE-MEYERING", "LAURA A")
    # IV after a space is kept on purpose (RAVIV guard in fec/config/data.py)
    assert _name(_donor(("W DAVERIO IV", "GEORGE", 1))) == ("DAVERIO IV", "GEORGE W")
    # the initial is not added twice
    assert _name(_donor(("K. CASTILLO", "KAREN K", 1))) == ("CASTILLO", "KAREN K")


def test_surname_particles_and_real_short_surnames_are_untouched():
    assert _name(_donor(("O BRIEN", "PATRICK", 1))) == ("O BRIEN", "PATRICK")
    assert _name(_donor(("D ANGELO", "MARIA", 1))) == ("D ANGELO", "MARIA")
    assert _name(_donor(("D SOUZA", "ANIL", 1))) == ("D SOUZA", "ANIL")
    assert _name(_donor(("L ESPERANCE", "ANNE", 1))) == ("L ESPERANCE", "ANNE")
    assert _name(_donor(("L HEUREUX", "ANNE", 1))) == ("L HEUREUX", "ANNE")
    assert _name(_donor(("O'BRIEN", "PATRICK", 1))) == ("O'BRIEN", "PATRICK")
    # L before a consonant is no particle (there is no L'COHEN): an initial
    assert _name(_donor(("L COHEN", "MICHAEL", 1))) == ("COHEN", "MICHAEL L")
    # D next to the donor's own clean filing is provably an initial
    assert _name(_donor(("D PERLMUTER", "MEIR", 2), ("PERLMUTER", "MEIR", 1))) == ("PERLMUTER", "MEIR")
    assert _name(_donor(("Y", "IVAN", 1))) == ("Y", "IVAN")
    assert _name(_donor(("NULL", "JAMES", 2))) == ("NULL", "JAMES")


# --- first name ----------------------------------------------------------


def test_noisy_minority_spelling_loses_to_the_clean_majority():
    assert _name(_donor(("LEVY", "ALLAN", 6), ("LEVY", "ALLAN -", 2)))[1] == "ALLAN"
    assert _name(_donor(("KOREN", "JOHN", 9), ("KOREN", "JOHN.", 1)))[1] == "JOHN"
    # a period after an initial is spelling, not noise: the old longest pick stays
    assert _name(_donor(("KORENSTEIN", "D", 2), ("KORENSTEIN", "D.", 1)))[1] == "D."
    assert _name(_donor(("SUSSER", "SAM L", 6), ("SUSSER", "SAM L.", 1)))[1] == "SAM L."


def test_spouse_or_nickname_parenthetical_does_not_win():
    """a76c217897fc: 10x 'KENNETH' + 1x 'KENNETH (ELLEN)' (ELLEN is the spouse)."""
    assert _name(_donor(("TAUBER", "KENNETH", 10), ("TAUBER", "KENNETH (ELLEN)", 1)))[1] == "KENNETH"
    assert _name(_donor(("ZUSMAN", "MICHAEL", 15), ("ZUSMAN", "MICHAEL (M.Z.)", 2)))[1] == "MICHAEL"
    # a 1:1 tie is not a reason to show the nickname either
    assert _name(_donor(("BROWNSTEIN", "HELEN (SUNNY)", 1), ("BROWNSTEIN", "HELEN", 1)))[1] == "HELEN"


def test_fullest_first_name_rule_still_adds_real_tokens():
    assert _name(_donor(("DOE", "MARK", 5), ("DOE", "MARK L.", 1)))[1] == "MARK L."
    # the donor-level name is still the fullest
    assert _canonical_person_name(["DOE"] * 3, ["FRANKLIN", "FRANKLIN", "FRANKLIN J. JAY"])[1] == "FRANKLIN J. JAY"
    # an initial expands only within filings that carry the same whole names
    df = _donor(("DOE", "MARVIN", 2), ("DOE", "M STEPHEN", 1), ("DOE", "MARVIN STEPHEN", 3))
    canonicalize_donor_names(df)
    assert df["contributor_first_name"].tolist() == ["MARVIN"] * 2 + ["MARVIN STEPHEN"] * 4
    # an initial alone takes the full name; a co-filer's name is never taken
    df = _donor(("DOE", "J", 1), ("DOE", "JOHN", 2), ("DOE", "SHIRA", 2), ("DOE", "SHIRA JARED", 1), key="K")
    canonicalize_donor_names(df)
    assert df["contributor_first_name"].tolist()[:3] == ["JOHN"] * 3
    # a nickname with another initial still unifies with the full name
    df = _donor(("DOE", "BILL", 2), ("DOE", "WILLIAM", 3))
    canonicalize_donor_names(df)
    assert set(df["contributor_first_name"]) == {"WILLIAM"}


def test_word_the_donor_brackets_elsewhere_is_an_aside():
    """8842ff7a49ea: 15x LYON, 3x 'LYON LENNY', 3x 'LYON (LENNY)': LENNY is a nickname."""
    assert _name(_donor(("ROTH", "LYON", 15), ("ROTH", "LYON LENNY", 3),
                        ("ROTH", "LYON (LENNY)", 3)))[1] == "LYON"
    assert _name(_donor(("WERNICK", "EPHRAIM", 17), ("WERNICK", "EPHRAIM FRY", 2),
                        ("WERNICK", "EPHRAIM (FRY)", 1)))[1] == "EPHRAIM"
    # never filed without the aside: the as-filed bracket form is kept
    assert _name(_donor(("ROTH", "LYON LENNY", 3), ("ROTH", "LYON (LENNY)", 3)))[1] == "LYON (LENNY)"


def test_sole_spelling_keeps_a_balanced_nickname():
    assert _name(_donor(("GERSON", "JAMES (JIM)", 2)))[1] == "JAMES (JIM)"


def test_sole_spelling_loses_broken_punctuation():
    assert _name(_donor(("SHER", "ANNA)", 2)))[1] == "ANNA"
    assert _name(_donor(("COHN", "ELAINE (MARK", 1)))[1] == "ELAINE"
    assert _name(_donor(("AIZER", "JEFFREY (YAKOV DOVBE", 1)))[1] == "JEFFREY"
    assert _name(_donor(("STEINBERG", "MIRIAM.", 4)))[1] == "MIRIAM"


def test_comma_fragment_in_first_name_is_dropped():
    """d4236ecf90fb: raw last 'HEY,AM', raw first 'DANIEL' was stored as first 'AM, DANIEL'."""
    assert _name(_donor(("HEY", "AM, DANIEL", 1))) == ("HEY", "DANIEL")


def test_joint_first_names_stay_as_filed():
    """Joint filings are never split or renamed (policy)."""
    assert _name(_donor(("ZELDIN", "STANFORD/JOYCE", 2)))[1] == "STANFORD/JOYCE"
    assert _name(_donor(("MEYERS", "STUART & SARA", 1)))[1] == "STUART & SARA"


def test_first_core_keeps_initial_and_abbreviation_periods():
    assert _first_core("MARK L.") == "MARK L."
    assert _first_core("WILLIAM JR.") == "WILLIAM JR."
    assert _first_core("JOHN.") == "JOHN"
    assert _first_core("(JIM)") == "JIM"
    # a suffix after a comma is not the first name
    assert _first_core("JAMES, JR.") == "JAMES"


def test_donor_key_and_other_donors_untouched():
    df = pd.concat([
        _donor(("A LEVY", "RICHARD", 2), ("LEVY", "RICHARD", 1), key="A"),
        _donor(("LEVY", "ALLAN -", 1), ("LEVY", "ALLAN", 3), key="B"),
    ], ignore_index=True)
    keys = df["donor_key"].tolist()
    canonicalize_donor_names(df)
    assert df["donor_key"].tolist() == keys
    assert set(df.loc[df["donor_key"] == "A", "contributor_name"]) == {"LEVY, RICHARD"}
    assert set(df.loc[df["donor_key"] == "B", "contributor_name"]) == {"LEVY, ALLAN"}


# --- organization names --------------------------------------------------


def _orgs(names, entity="ORGANIZATION"):
    return pd.DataFrame(
        {
            "entity_type": [entity] * len(names),
            "contributor_name": names,
            "donor_key": [f"k{i}" for i in range(len(names))],
        }
    )


def test_org_name_with_legal_form_takes_the_suffix_free_spelling():
    df = _orgs(["EATON STEEL", "EATON STEEL CORPORATION"])
    assert unify_org_donor_suffix_variants(df) == 1
    assert df["contributor_name"].tolist() == ["EATON STEEL", "EATON STEEL"]
    assert df["donor_key"].tolist() == ["k0", "k1"]


def test_org_suffix_needs_the_bare_spelling_on_file():
    df = _orgs(["ACME CORPORATION", "ACME GROUP"])
    assert unify_org_donor_suffix_variants(df) == 0
    df = _orgs(["EATON STEEL", "EATON STEEL CORPORATION"], entity="COMMITTEE/PAC")
    assert unify_org_donor_suffix_variants(df) == 0


# --- donor stage ordering ------------------------------------------------


def _person(sub_id, name, employer):
    last, first = (part.strip() for part in name.split(",", 1))
    return {
        "sub_id": sub_id, "transaction_id": f"T{sub_id}", "two_year_transaction_period": 2024,
        "recipient_committee": "AIPAC", "entity_type": "INDIVIDUAL",
        "contributor_name": name, "contributor_first_name": first, "contributor_last_name": last,
        "contributor_street_1": f"{sub_id} MAIN ST", "contributor_street_2": None,
        "contributor_city": "LOS ANGELES", "contributor_state": "CA", "contributor_zip": "90067",
        "contributor_employer": employer, "contributor_occupation": "ATTORNEY",
        "occupation_category": "LEGAL", "occupation_status": "DISCLOSED", "committee_type": None,
        "contribution_receipt_date": "2024-03-05", "contribution_receipt_amount": 100.0,
        "donor_key": f"p{sub_id}", "previous_employer": None,
    }


def test_org_alignment_sees_entity_overrides_and_final_employer(tmp_path, monkeypatch):
    """Pachulski: the override makes the row an ORGANIZATION only in donor consistency,
    so the alignment must run after it (and after employer finalisation)."""
    from fec.cleaning.donor_consistency import entity
    from fec.cleaning.pipeline.core import standardize_donors

    (tmp_path / "data" / "database").mkdir(parents=True)
    (tmp_path / "data" / "database" / "entity_overrides.csv").write_text(
        'contributor_name,entity_type,note\n"PACHULSKI STANG ZIEHL & JONES",ORGANIZATION,law firm\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(entity, "PROJECT_ROOT", tmp_path)

    firm = "PACHULSKI, STANG, ZIEHL & JONES"
    rows = [_person(str(i), name, firm) for i, name in enumerate(
        ["DOE, JANE", "ROE, RICHARD", "POE, ALAN"], start=1)]
    org = _person("9", "X, Y", None)
    org.update({
        "entity_type": "COMMITTEE/PAC", "contributor_name": "PACHULSKI STANG ZIEHL & JONES",
        "contributor_first_name": None, "contributor_last_name": None,
        "contributor_occupation": None, "occupation_category": "POLITICAL COMMITTEE",
        "occupation_status": "NOT_APPLICABLE", "donor_key": "orgkey",
    })
    result = standardize_donors(pd.DataFrame(rows + [org]), out_dir=str(tmp_path))

    org_row = result[result["sub_id"] == "9"].iloc[0]
    assert org_row["entity_type"] == "ORGANIZATION"
    assert org_row["contributor_name"] == firm
    assert org_row["donor_key"] == "orgkey"


def test_filed_given_names_beyond_the_first_name_field_are_kept():
    from fec.cleaning.pipeline.names import _keep_filed_given_names

    df = pd.DataFrame({
        'contributor_name': ['HARRIS, S. WOLF', 'MORRIS, ELLEN STUN', 'SMITH, J',
                             'DOE, J JANE', 'ROE, JOHN MD', 'LEE, ANN', 'WINN, RANDALL RANDALL'],
        'contributor_first_name': ['S.', 'ELLEN', 'J', 'K', 'JOHN', 'ANN', 'RANDALL'],
        'contributor_last_name': ['HARRIS', 'MORRIS', 'SMITH', 'DOE', 'ROE', 'LEE', 'WINN'],
    })
    _keep_filed_given_names(df, pd.Series(True, index=df.index))
    assert df['contributor_first_name'].tolist() == [
        'S. WOLF', 'ELLEN STUN', 'J', 'K', 'JOHN', 'ANN', 'RANDALL',
    ]
