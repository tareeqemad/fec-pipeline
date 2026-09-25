"""Joint filings keep each person's own name (owner decision 2026-09-23).

A joint filing ("SPELLMAN, MARC MELISSA") stays as filed on its own donor_key;
the partner's given name is never written onto the filer's solo filings.
Whether a second word is a co-filer or the filer's own middle name is decided
from the household's own filings (fec/donor_match/joint.py). Every fixture is
a real pattern from data/contributions.csv (joint-name audit 2026-09-23).
"""
import pandas as pd
import pytest

from fec.donor_match import apply_donor_key, match_donors
from fec.donor_match import matcher as M
from fec.donor_match.canonicalize import canonicalize_donor_names
from fec.donor_match.joint import given_tokens, joint_partners


def _t(*names):
    return {given_tokens(name) for name in names}


# -- the per-household decision ------------------------------------------------

def test_spouse_who_files_alone_makes_the_word_a_co_filer():
    # SPELLMAN: Melissa files alone 39 times from the same street
    assert joint_partners(given_tokens("MARC MELISSA"), _t("MARC", "MARC MELISSA"),
                          _t("MELISSA")) == {"MELISSA"}
    # RUB: Marta Rub files alone from Beny's office address
    assert joint_partners(given_tokens("BENY MARTA"), _t("BENY"), _t("MARTA")) == {"MARTA"}


def test_nickname_of_a_household_member_counts():
    # MORRIS: "ELLEN STU" and glued "ELLENSTU"; Stuart Morris files alone
    assert joint_partners(given_tokens("ELLEN STU"), _t("ELLEN"), _t("STUART")) == {"STUART"}
    assert joint_partners(given_tokens("ELLENSTU"), _t("ELLEN STU", "ELLENSTU"),
                          _t("STUART")) == {"STUART"}


def test_no_one_in_the_household_files_the_word_so_it_is_a_middle_name():
    # KAPNER "HILARY SMITH", MICHAN before the household is known
    assert joint_partners(given_tokens("HILARY SMITH"), _t("HILARY"), set()) == frozenset()
    assert joint_partners(given_tokens("HILARY SMITH"), _t("HILARY"), _t("JOHN")) == frozenset()


def test_initial_forms_make_the_word_the_filers_own():
    # HUTSON: "L ROGER" on Lowell's own key; the ROGER key is the same man
    assert joint_partners(given_tokens("LOWELL ROGER"), _t("LOWELL", "L ROGER"),
                          _t("ROGER")) == frozenset()
    # SCHARF: "Y. DAVID" on another key at 545 W END AVE (Y. David Scharf)
    assert joint_partners(given_tokens("YEHUDA DAVID"), _t("YEHUDA"),
                          _t("DAVID", "Y. DAVID")) == frozenset()
    # REISS: the first "name" is only an initial
    assert joint_partners(given_tokens("M FREDDIE"), _t("M"), _t("FREDDIE")) == frozenset()


def test_one_man_ordering_his_names_both_ways_is_not_a_couple():
    # DENNIS 289 BROOKSIDE AVE: "STEVE MARVIN" next to MARVIN / "MARVIN STEPHEN" / "M STEPHEN"
    household = _t("MARVIN", "MARVIN STEPHEN", "M STEPHEN")
    assert joint_partners(given_tokens("STEVE MARVIN"), _t("STEPHEN"), household) == frozenset()


def test_couple_filing_in_both_orders_stays_joint():
    # MEYERS 1841 VERMACK CT: no initial form ties Sara to Stuart's name
    assert joint_partners(given_tokens("STUART SARA"), _t("STUART"),
                          _t("SARA", "SARA STUART")) == {"SARA"}


def test_word_that_never_files_alone_is_not_a_co_filer():
    # a household JAY who only ever files together with PAUL is not a second person
    assert joint_partners(given_tokens("PAUL JAY"), _t("PAUL"), _t("JAY PAUL")) == frozenset()


def test_newman_needs_its_verified_rule():
    """NEWMAN 8501 CHALK KNOLL DR: by the time donors are matched the JAY rows
    read 'NEWMAN, JAY' (FEC first-name field), so the household alone makes
    'PAUL JAY' look joint. FEC shows one THRIVE FP CEO under JAY PAUL, PAUL JAY,
    PAUL, JAY P. and JP; the verified merge_keys rule makes JAY his own record."""
    from fec.donor_match.rules import resolve_donor_key

    assert joint_partners(given_tokens("PAUL JAY"), _t("PAUL"), _t("JAY")) == {"JAY"}
    assert resolve_donor_key("58f2f2117f31") == "57623038bd1a"


def test_own_nickname_is_not_a_co_filer():
    assert joint_partners(given_tokens("STEPHEN STEVE"), _t("STEPHEN"), _t("STEVE")) == frozenset()


# -- donor matching ------------------------------------------------------------

def _row(first, city, zip5, street, employer="", occupation="RETIRED", category="RETIRED"):
    return dict(
        entity_type="INDIVIDUAL", contributor_name=f"SPELLMAN, {first}",
        contributor_first_name=first, contributor_last_name="SPELLMAN",
        contributor_city=city, contributor_state="IL", contributor_zip=zip5,
        contributor_street_1=street, contributor_employer=employer,
        contributor_occupation=occupation, occupation_category=category,
        _generational_suffix="",
    )


def _spellman():
    """The household as filed: Marc at two addresses, one joint row, Melissa alone."""
    rows = [_row("MARC", "GLENCOE", "60022", "325 SHORELINE CT")] * 2
    rows += [_row("MARC", "NORTHBROOK", "60062", "4 BRIDLEWOOD RD", "IMPERIAL GROUP",
                  "PRESIDENT", "EXECUTIVE / C-SUITE")] * 2
    rows += [_row("MARC MELISSA", "GLENCOE", "60022", "325 SHORELINE CT", "IMPERIAL GROUP",
                  "METAL TRADER", "BUSINESS / ENTREPRENEUR")]
    rows += [_row("MELISSA", "GLENCOE", "60022", "325 SHORELINE CT", "NOT EMPLOYED",
                  "NOT EMPLOYED", "NOT EMPLOYED")] * 25
    rows += [_row("MELISSA", "NORTHBROOK", "60062", "4 BRIDLEWOOD RD", "NOT EMPLOYED",
                  "NOT EMPLOYED", "NOT EMPLOYED")] * 14
    return pd.DataFrame(rows)


def _keys(df):
    rid_to_key, _ = match_donors(df)
    df = apply_donor_key(df, rid_to_key)
    return df, df.groupby("contributor_name")["donor_key"].agg(set)


def test_joint_row_gets_its_own_key_and_marc_stays_one_donor():
    df, keys = _keys(_spellman())
    marc, joint, melissa = (keys["SPELLMAN, MARC"], keys["SPELLMAN, MARC MELISSA"],
                            keys["SPELLMAN, MELISSA"])
    assert len(marc) == 1 and len(joint) == 1 and len(melissa) == 1
    assert not joint & (marc | melissa) and marc != melissa
    # the joint filing still linked Marc's two addresses (it is his filing too)
    names = df.groupby("donor_key")["contributor_name"].agg(set)
    assert names[next(iter(marc))] == {"SPELLMAN, MARC"}


def test_without_the_guard_the_joint_row_lands_on_marcs_key(monkeypatch):
    """The bad case the guard exists for (HEAD of 2026-09-23)."""
    monkeypatch.setattr(M, "find_joint_filings", lambda profiles, components: {})
    _, keys = _keys(_spellman())
    assert keys["SPELLMAN, MARC MELISSA"] <= keys["SPELLMAN, MARC"]


def test_joint_part_keeps_the_union_find_root():
    components = {"J": {"J", "A", "B"}, "X": {"X"}}
    profiles = {rid: {"record_count": n} for rid, n in (("J", 1), ("A", 2), ("B", 2), ("X", 1))}
    moved = M.split_joint_filings(components, {"J": frozenset({"MELISSA"})}, profiles)
    assert moved == 1
    assert components == {"J": {"J"}, "B": {"A", "B"}, "X": {"X"}}


# -- canonical display name (the fallback when a joint row shares a key) --------

COLS = ["entity_type", "donor_key", "contributor_name", "contributor_first_name",
        "contributor_last_name", "contributor_street_1", "contributor_zip"]


def _rows(key, first, n, last="SPELLMAN", street="325 SHORELINE CT", zip5="60022"):
    return [["INDIVIDUAL", key, f"{last}, {first}", first, last, street, zip5]] * n


def test_partners_name_is_not_stamped_on_solo_filings():
    df = pd.DataFrame(_rows("MARC", "MARC", 4) + _rows("MARC", "MARC MELISSA", 1)
                      + _rows("MEL", "MELISSA", 39), columns=COLS)
    canonicalize_donor_names(df)
    marc = df.loc[df.donor_key == "MARC", "contributor_first_name"].tolist()
    # the solo filings stay MARC; the joint filing keeps both names as filed
    assert marc == ["MARC"] * 4 + ["MARC MELISSA"]


def test_a_whole_extra_name_stays_on_the_filings_that_have_it():
    # LOUIS may be a middle name or a co-filer: no filing gains or loses it
    df = pd.DataFrame(_rows("J", "JOSEPH", 26, last="SHAMIE") + _rows("J", "JOSEPH LOUIS", 2, last="SHAMIE")
                      + _rows("S", "SAM", 14, last="SHAMIE", street="39 COLIN PL"), columns=COLS)
    canonicalize_donor_names(df)
    assert df.loc[df.donor_key == "J", "contributor_first_name"].tolist() == ["JOSEPH"] * 26 + ["JOSEPH LOUIS"] * 2


def test_a_co_filers_name_is_not_spread_onto_solo_filings():
    df = pd.DataFrame(_rows("B", "SHIRA", 2, last="BOSCHAN") + _rows("B", "SHIRA JARED", 1, last="BOSCHAN"),
                      columns=COLS)
    canonicalize_donor_names(df)
    assert df.loc[df.donor_key == "B", "contributor_first_name"].tolist() == ["SHIRA", "SHIRA", "SHIRA JARED"]


def test_a_joint_only_donor_keeps_its_joint_name():
    df = pd.DataFrame(_rows("J", "MARC MELISSA", 1) + _rows("MEL", "MELISSA", 5), columns=COLS)
    canonicalize_donor_names(df)
    assert df.loc[df.donor_key == "J", "contributor_first_name"].tolist() == ["MARC MELISSA"]


def test_review_does_not_list_an_intended_joint_split():
    from fec.donor_match.dedup_review import _is_joint_pair

    # SPELLMAN 60022: MARC / MARC MELISSA next to MELISSA
    assert _is_joint_pair("MARC", "MARC MELISSA", {"MARC", "MARC MELISSA", "MELISSA"})
    # HELLER 48070: GAYLE / GAYLEDAVID next to DAVID
    assert _is_joint_pair("GAYLEDAVID", "GAYLE", {"GAYLE", "GAYLEDAVID", "DAVID"})
    # without the co-filer in the ZIP the pair stays in the review
    assert not _is_joint_pair("MARC", "MARC MELISSA", {"MARC", "MARC MELISSA"})
    assert not _is_joint_pair("MARY", "MARYANN", {"MARY", "MARYANN", "JOHN"})


@pytest.mark.parametrize("name", ["GOTTESMAN, MARGERY ARCHIE", "MICHAN, CARLOS DAVID"])
def test_verified_rules_mark_the_word_as_the_filers_own(name):
    from fec.donor_match.rules import joint_name_exempt

    assert joint_name_exempt(name)
    assert not joint_name_exempt("SPELLMAN, MARC MELISSA")
