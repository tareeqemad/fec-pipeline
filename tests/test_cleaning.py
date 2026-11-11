"""Tests of the cleaning stage.

Names, entity types, work fields, categories and filing corrections.
"""

import pandas as pd
import pytest

from fec.cleaning import pipeline as cleaning_pipeline
from fec.cleaning.work.company_names import apply_reviewed_employer_names
from fec.common.columns import CLEAN_COLUMNS


def test_failed_quality_gates_keep_the_previous_cleaned_file(tmp_path, monkeypatch):
    output = tmp_path / "contributions_cleaned.csv"
    output.write_text("previous good file\n", encoding="utf-8")
    cleaned = pd.DataFrame(
        dict.fromkeys(CLEAN_COLUMNS, [""])
        | {
            "sub_id": ["1"],
            "recipient_committee": [None],
            "contributor_zip": ["60601"],
            "contributor_city": ["CHICAGO"],
            "contributor_state": ["IL"],
            "contributor_street_1": ["1 MAIN ST"],
            "contributor_street_2": [""],
        }
    )
    monkeypatch.setattr(cleaning_pipeline, "CLEANED_CSV", output)
    monkeypatch.setattr(cleaning_pipeline, "read_pipeline_csv", lambda path: cleaned.copy())
    monkeypatch.setattr(cleaning_pipeline, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(cleaning_pipeline, "clean_and_match", lambda df, **_dirs: df)
    monkeypatch.setattr(
        cleaning_pipeline,
        "run_quality_gates",
        lambda df: {"passed": False, "checks": {}, "issues": ["1 rows with a missing amount"]},
    )

    with pytest.raises(SystemExit):
        cleaning_pipeline.main()

    assert output.read_text(encoding="utf-8") == "previous good file\n"
    assert (tmp_path / "quality_gates.json").exists()


def test_reviewed_company_names_rewrite_short_and_misspelt_employers():
    df = pd.DataFrame(
        {
            "entity_type": ["INDIVIDUAL"] * 6,
            "contributor_employer": [
                "SPGI",
                "NMRK",
                "JPMC",
                "C21 STORES",
                "ABINITIO SOFTWARE",
                "SOFTERWARE",
            ],
        }
    )
    apply_reviewed_employer_names(df, ("contributor_employer",))
    assert df["contributor_employer"].tolist() == [
        "S&P GLOBAL",
        "NMRK",
        "JPMC",
        "CENTURY 21 STORES",
        "AB INITIO SOFTWARE",
        "SOFTERWARE",
    ]


def test_company_evidence_levels():
    from fec.cleaning.work.company_evidence import Filings, company_evidence_level

    values = pd.Series(
        [
            "ACME, L.L.C.",
            "KIRKLAND & ELLIS LLP",
            "BROWN & BROWN",
            "REAL ESTATE",
            "INVESTMENT PORTFOLIO MANAGER",
            "VENTURE CAPITALIST",
            "CHAIRMAN AND CEO",
            "DIRECTOR OF POLICY",
            "CRESCENT REALTY ADVISORS",
            "ZORVAL CAPITAL",
            "ZORVAL & TARQUIN",
            "WEALTH MANAGEMENT",
            "MEDSTAR HEALTH",
            "MATTEL",
            "",
        ]
    )
    assert values.map(company_evidence_level).tolist() == [
        "legal_form",
        "legal_form",
        "reviewed",
        "job",
        "job",
        "job",
        "job",
        "job",
        "named_org",
        "named_org",
        "named_org",
        "job",
        "named_org",
        "unknown",
        "",
    ]

    # Other donors filing the value as their employer lifts an unknown name.
    employers = pd.Series(["MATTEL", "MATTEL", "MATTEL", "REAL ESTATE MANAGEMENT", "REAL ESTATE MANAGEMENT", "ME"])
    filers = pd.Series(["A", "B", "C", "D", "E", "F"])
    filings = Filings(filers, employers)
    assert [filings.level(text, 2) for text in ("MATTEL", "REAL ESTATE MANAGEMENT", "ME")] == [
        "frequent",
        "job",
        "unknown",
    ]


def test_entity_type_from_committee_signal_and_company_evidence():
    from fec.cleaning.entity_types import classify_entities, is_committee_name

    names = [
        "BANK, ELLYN",
        "KAHAN TRUST, DAVID",
        "BRITT FOR ALABAMA INC, KATIE MS.",
        "SUNPAC, SUNPAC",
        "AMALGAMATED BANK",
        "KRAFT GROUP LLC",
        "CLEVELAND BUILDING AND CONSTRUCTION TRADES COUNCIL",
        "SMITH, JOHN",
        # The filer's own flag keeps a person whose name box holds an organisation word.
        "KLAIMAN OFFICE, ALLAN",
        "BELLFORMISSOURI, WESLEY MR",
    ]
    df = pd.DataFrame(
        {
            "sub_id": [str(i) for i in range(len(names))],
            "contributor_name": names,
            "contributor_first_name": [
                "ELLYN",
                "DAVID",
                "KATIE",
                "",
                "",
                "",
                "",
                "JOHN",
                "ALLAN",
                "WESLEY",
            ],
            "contributor_last_name": [
                "BANK",
                "KAHAN TRUST",
                "BRITT FOR ALABAMA INC",
                "",
                "",
                "",
                "",
                "SMITH",
                "KLAIMAN OFFICE",
                "BELLFORMISSOURI",
            ],
            "contributor_employer": [
                "ACME LLC",
                "",
                "",
                "",
                "",
                "",
                "",
                "ACME LLC",
                "UROLOGY CENTER",
                "",
            ],
            "contributor_occupation": [
                "ANALYST",
                "",
                "",
                "",
                "",
                "",
                "",
                "ANALYST",
                "MEDICAL DOCTOR",
                "",
            ],
            "occupation_category": [None] * len(names),
            "is_individual": [False, False, False, True, False, True, False, False, True, True],
        }
    )

    classify_entities(df)

    assert df["entity_type"].tolist() == [
        "INDIVIDUAL",
        "ORGANIZATION",
        "COMMITTEE/PAC",
        "COMMITTEE/PAC",
        "ORGANIZATION",
        "ORGANIZATION",
        "ORGANIZATION",
        "INDIVIDUAL",
        "INDIVIDUAL",
        "COMMITTEE/PAC",
    ]
    assert df["is_individual"].tolist() == df["entity_type"].eq("INDIVIDUAL").tolist()
    assert is_committee_name("TEAM HAGERTY, BILL SEN.") and is_committee_name("DON DAVIS FOR NC, DON")
    assert not is_committee_name("BANK OF AMERICA") and not is_committee_name("SMITH, JOHN")


def test_split_retired_reads_every_spelling():
    from fec.cleaning.work.retired import split_retired

    assert split_retired("RETIRED") == (True, "")
    assert split_retired("TETIRED") == (True, "")
    assert split_retired("SEMI-RETIRED") == (True, "")
    assert split_retired("RETIRED FROM DAIICHI SANKYO") == (True, "DAIICHI SANKYO")
    assert split_retired("RETIRED - ORACLE") == (True, "ORACLE")
    assert split_retired("UCLA RETIRED") == (True, "UCLA")
    assert split_retired("PHYSICIAN, RETIRED") == (True, "PHYSICIAN")
    assert split_retired("RETIRED ATTORNEY") == (True, "ATTORNEY")
    assert split_retired("I'M RETIRED") == (True, "I'M")
    assert split_retired("RETIREDPHYSICIAN") == (True, "PHYSICIAN")
    # RETIRED inside one part qualifies that part only: the role before it is current.
    assert split_retired("OFFICE MANAGER, RETIRED NURSE") == (False, "OFFICE MANAGER")
    assert split_retired("VOLUNTEER (RETIRED ATTORNEY)") == (False, "VOLUNTEER")
    assert split_retired("ARBITRATOR/RETIRED JUDGE") == (False, "ARBITRATOR")
    assert split_retired("HOMEMAKER/WIFE OF RETIRED MILITARY MEM") == (False, "HOMEMAKER")
    assert split_retired("SELF, RETIRED TEACHER") == (True, "SELF, TEACHER")
    assert split_retired("TEACHER NOW RETIRED CONSULTANT") == (True, "TEACHER NOW CONSULTANT")
    assert split_retired("RETIREMENT PLANNER") == (False, "RETIREMENT PLANNER")
    assert split_retired("BOARD OF RETIRED") == (False, "BOARD OF RETIRED")
    assert split_retired("ACME LLC") == (False, "ACME LLC")


def test_read_retired_keeps_job_and_company():
    from fec.cleaning.work.retired import read_retired

    # (employer, occupation) -> (employer, occupation, former company, said retired)
    assert read_retired("RETIRED FROM DAIICHI SANKYO", "RETIRED") == (
        "RETIRED",
        "RETIRED",
        "DAIICHI SANKYO",
        True,
    )
    assert read_retired("RETIRE", "SOCIAL WORKER") == ("RETIRED", "SOCIAL WORKER", "", True)
    assert read_retired("RETIRED TEACHER", "RETIRED") == ("RETIRED", "TEACHER", "", True)
    assert read_retired("I'M RETIRED", "RETIRED") == ("RETIRED", "RETIRED", "", True)
    assert read_retired("RETIRED", "RETIRED ATTORNEY") == ("RETIRED", "ATTORNEY", "", True)
    assert read_retired("ACME LLC", "ENGINEER") == ("ACME LLC", "ENGINEER", "", False)
    # a current role before a retired one: still working
    assert read_retired("STEVEN TENNENBAUM", "OFFICE MANAGER, RETIRED NURSE") == (
        "STEVEN TENNENBAUM",
        "OFFICE MANAGER",
        "",
        False,
    )
    assert read_retired("", "VOLUNTEER (RETIRED ATTORNEY)") == ("", "VOLUNTEER", "", False)
    # JAMS stays here; the donor step moves a retiree's company to previous_employer.
    assert read_retired("JAMS", "RETIRED JUDGE MEDIATOR") == ("JAMS", "JUDGE MEDIATOR", "", True)


def test_split_self_marker_and_previous_employer():
    from fec.cleaning.work.self_employed import split_self_marker
    from fec.cleaning.work.work_fields import company_named

    assert split_self_marker("SELF - ACME LLC") == (True, "ACME LLC")
    assert split_self_marker("SELF; JACK A FRYDRYCH APLC") == (True, "JACK A FRYDRYCH APLC")
    assert split_self_marker("SELF EMPLOYED AND TOTAL REALTY") == (True, "TOTAL REALTY")
    assert split_self_marker("CONSULTANT (SELF-EMPLOYED)") == (True, "CONSULTANT")
    assert split_self_marker("SELF") == (True, "")
    assert split_self_marker("ACME LLC") == (False, "")
    assert company_named("SELF; JACK A FRYDRYCH APLC") == "JACK A FRYDRYCH APLC"
    assert company_named("SELF - ROUND ROCK PERIODONTICS") == "ROUND ROCK PERIODONTICS"
    assert company_named("SELF EMPLOYED; DENTIST") == ""  # a trade, no company


def test_learn_typos_reads_one_slip_from_one_listed_occupation():
    from fec.cleaning.work.occupation_typos import learn_typos

    listed = [
        "ATTORNEY",
        "CONSULTANT",
        "PHYSICIAN",
        "PARTNER",
        "ARCHITECT",
        "CEO OWNER",
        "CO-OWNER",
        "RETIRED",
    ]
    texts = pd.Series(
        [
            "ATTORNEYY",
            "CONSULTATN",
            "PHYSICAIN ",
            "ARCHITECTS",  # added, transposed, swapped, plural
            # one slip from PARTNER: read, which is why only the occupation box is learnt from
            "GARTNER",
            "ATTORNEY",  # listed already
            "CO OWNER",  # CO / CEO: a word under six letters
            "ATTORNEY AT LAW",  # two words more, not a slip
            "XX",  # junk
            "RETIRE",  # a status word: the RETIRED reader owns it
        ]
    )
    assert learn_typos(texts, listed=listed) == {
        "ATTORNEYY": "ATTORNEY",
        "CONSULTATN": "CONSULTANT",
        "PHYSICAIN": "PHYSICIAN",
        "ARCHITECTS": "ARCHITECT",
        "GARTNER": "PARTNER",
    }


def test_extend_corrections_adds_learnt_typos_to_the_table():
    from fec.cleaning.work.occupation_typos import extend_corrections
    from fec.vocabulary.occupations import OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS

    df = pd.DataFrame({"contributor_occupation": ["ORTHODONTISTT", "ATTORNEY"], "contributor_employer": ["", ""]})
    try:
        assert extend_corrections(df) == 1
        assert (
            OCCUPATION_CORRECTIONS["ORTHODONTISTT"] == "ORTHODONTIST"
            and TEXT_CORRECTIONS["ORTHODONTISTT"] == "ORTHODONTIST"
        )
    finally:
        for table in (OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS):
            table.pop("ORTHODONTISTT", None)


def test_an_unlisted_occupation_takes_the_category_of_its_last_listed_word():
    from fec.cleaning.work.occupation_categories import category_of, lookup_categories

    assert category_of("ATTORNEY") == "LEGAL"  # in the table
    assert category_of("PEDIATRIC CARDIAC SURGEON") == "MEDICAL / HEALTHCARE"  # SURGEON
    assert category_of("QUANTUM TOPOLOGIST") == "OTHER"  # no listed word
    assert lookup_categories(pd.Series(["ATTORNEY", None])).tolist() == ["LEGAL", pd.NA]


def test_every_category_in_the_tables_is_a_known_category():
    from fec.vocabulary.occupations import OCCUPATION_CATEGORIES, OCCUPATION_CATEGORY_WORDS, VALID_CATEGORIES

    assert set(OCCUPATION_CATEGORIES.values()) <= VALID_CATEGORIES
    assert set(OCCUPATION_CATEGORY_WORDS.values()) <= VALID_CATEGORIES


def test_status_word_reads_a_slip_and_leaves_other_words():
    from fec.cleaning.work.status_spellings import learn_status_typos, status_word

    assert status_word("RETIERD") == "RETIRED"
    assert status_word("RETRIED") == "RETIRED"  # transposition
    assert status_word("SE;F EMPLOYED") == "SELF-EMPLOYED"  # a wrong key
    assert status_word("SELF-EMPLO") == "SELF-EMPLOYED"  # truncated
    assert status_word("NOT EMOLOYED") == "NOT EMPLOYED"
    assert status_word("HOUSE WIFE") == "HOUSEWIFE"
    assert status_word("RETIRED") == "RETIRED"  # itself
    for word in (
        "PRESIDENT",
        "RESIDENT",
        "RETAILER",
        "HOMEOWNER",
        "STUDIO",
        "RET",
        "SELF",
        "RETIREMENT PLANNER",
    ):
        assert status_word(word) is None, word
    assert learn_status_typos(
        ["Self Employeed", "UN EMPLOYED", "ACME LLC", "RETIRED"],
        canonical={"UNEMPLOYED": "NOT EMPLOYED"},
    ) == {
        "SELF EMPLOYEED": "SELF-EMPLOYED",
        "UN EMPLOYED": "NOT EMPLOYED",
    }


def _read(rows):
    """(employer, occupation) as read for filings.

    Each filing is given as (name, filed employer, filed occupation).
    """
    from fec.cleaning.work.work_fields import read_work

    df = pd.DataFrame(rows, columns=["contributor_name", "filed_employer", "filed_occupation"])
    df["entity_type"] = "INDIVIDUAL"
    df["contributor_last_name"] = df["contributor_name"].str.split(", ").str[0]
    df["contributor_first_name"] = df["contributor_name"].str.split(", ").str[1]
    df["contributor_employer"] = df["contributor_occupation"] = df["occupation_category"] = None
    read_work(df, df["entity_type"].eq("INDIVIDUAL"), df["filed_employer"], df["filed_occupation"])
    return list(zip(df["contributor_employer"].fillna(""), df["contributor_occupation"].fillna("")))


def test_work_fields_are_read_from_the_filed_pair():
    cases = {
        # each field on its own
        ("A, B", "SELF", "ATTORNEY"): ("SELF-EMPLOYED", "ATTORNEY"),
        ("A, B", "ACME LLC", "ATTY"): ("ACME LLC", "ATTORNEY"),
        ("A, B", "ACME LLC", "XXX"): ("ACME LLC", ""),
        ("A, B", "", "RETIRED TEACHER"): ("RETIRED", "TEACHER"),
        ("A, B", "UCLA RETIRED", "PROFESSOR"): ("RETIRED", "PROFESSOR"),
        # a status in one box fills the other
        ("A, B", "HOUSEWIFE", ""): ("NOT EMPLOYED", "HOMEMAKER"),
        ("A, B", "NONE", "HOMEMAKER"): ("NOT EMPLOYED", "HOMEMAKER"),
        ("A, B", "SELF EMPLOYEED", ""): ("SELF-EMPLOYED", ""),
        # an employer that names none
        ("A, B", "NOTYOURBUSINESS", "NOTYOURBUSINESS"): ("", ""),
        ("A, B", "VOLUNTEER", "VOLUNTEER"): ("", "VOLUNTEER"),
        ("A, B", "43209876", "LIBRARIAN"): ("", "LIBRARIAN"),
        ("A, B", "A@B.COM", "ENGINEER"): ("", "ENGINEER"),
        ("A, B", "GE", "ENGINEER"): ("GE", "ENGINEER"),
        # a job in the employer box: swapped with a company, never a guessed SELF-EMPLOYED
        ("A, B", "ATTORNEY", "WOLF, RIFKIN"): ("WOLF RIFKIN", "ATTORNEY"),
        ("A, B", "CARDIOLOGIST", "CARDIOLOGY PC"): ("CARDIOLOGY PC", "CARDIOLOGIST"),
        ("A, B", "DESIGNER", "SELF-EMPLOYED"): ("SELF-EMPLOYED", "DESIGNER"),
        ("A, B", "ATTORNEY", ""): ("", "ATTORNEY"),
        ("A, B", "OWNER", ""): ("SELF-EMPLOYED", "OWNER"),
        ("A, B", "ATTORNEY", "RETIRED"): ("RETIRED", "ATTORNEY"),
        ("A, B", "CONSULTANT", "INVESTOR"): ("", "INVESTOR"),
        ("A, B", "CONSULTANT", "PROJECT MANAGEMENT CONSULTANT"): (
            "",
            "PROJECT MANAGEMENT CONSULTANT",
        ),
        ("A, B", "ATTORNEY", "ATTORNEY"): ("", "ATTORNEY"),
        # a company in the occupation box: the role stays, the company moves to an open employer
        ("A, B", "", "PRESIDENT, RELAY HUMAN CLOUD, INC"): ("RELAY HUMAN CLOUD INC", "PRESIDENT"),
        ("A, B", "BOXER PROPERTY CORP", "CEO AT ACME LLC"): ("BOXER PROPERTY CORP", "CEO AT ACME LLC"),
        ("A, B", "", "MD ANDERSON CANCER CENTER INC"): ("MD ANDERSON CANCER CENTER INC", ""),
        ("A, B", "ACME LLC", "PRESIDENT OF HEALTH TECH COMPANY"): (
            "ACME LLC",
            "PRESIDENT OF HEALTH TECH COMPANY",
        ),
        ("A, B", "SELF-EMPLOYED", "COMPANY"): ("SELF-EMPLOYED", ""),
        # the donor's own name is no employer, unless it carries a legal form
        ("DOE, JANE", "JANE DOE", "WRITER"): ("SELF-EMPLOYED", "WRITER"),
        ("DOE, JANE", "JANE DOE LLC", "WRITER"): ("JANE DOE LLC", "WRITER"),
        # the occupation is a name this donor files as their employer elsewhere
        ("BRUST, RANDY", "SELF-EMPLOYED", "CRESCENT REALTY ADVISORS"): (
            "CRESCENT REALTY ADVISORS",
            "",
        ),
        ("BRUST, RANDY", "CRESCENT REALTY ADVISORS", "REAL ESTATE"): (
            "CRESCENT REALTY ADVISORS",
            "REAL ESTATE",
        ),
    }
    assert dict(zip(cases, _read(list(cases)))) == cases


def test_placeholder_employers_are_one_list():
    from fec.cleaning.work.work_fields import names_no_employer

    values = ["43209876", "A@B.COM", "XXX", "NM", "GE", "N A", "PRIVATE", "ACME LLC"]
    assert [names_no_employer(v, also_words=False) for v in values] == [
        True,
        True,
        True,
        True,
        False,
        False,
        False,
        False,
    ]
    assert [names_no_employer(v) for v in values] == [
        True,
        True,
        True,
        True,
        False,
        True,
        True,
        False,
    ]


def test_occupation_corrections_are_one_table():
    from fec.vocabulary.occupations import OCCUPATION_CORRECTIONS, TEXT_CORRECTIONS

    # No correction leads to another listed form: a filed value is corrected in one step.
    assert not [
        k for k, v in OCCUPATION_CORRECTIONS.items() if v in OCCUPATION_CORRECTIONS and OCCUPATION_CORRECTIONS[v] != v
    ]
    assert OCCUPATION_CORRECTIONS["ATTY"] == "ATTORNEY" and "ATTY" in TEXT_CORRECTIONS
    assert OCCUPATION_CORRECTIONS["XXX"] is None and "XXX" not in TEXT_CORRECTIONS


def test_a_name_is_read_once_from_its_three_boxes():
    from fec.cleaning.contributor_names import read_name

    assert read_name("GALICI, SR., DR. VINCENT", "DR. VINCENT", "GALICI, SR.") == (
        "GALICI, VINCENT",
        "VINCENT",
        "GALICI",
    )
    assert read_name("POLLOCK MD, TODD", "TODD", "POLLOCK MD") == (
        "POLLOCK, TODD",
        "TODD",
        "POLLOCK",
    )
    assert read_name("GERENRAICH, DPM, KENNY", "KENNY", "GERENRAICH, DPM") == (
        "GERENRAICH, KENNY",
        "KENNY",
        "GERENRAICH",
    )
    assert read_name("YOUNGMAN, CARL M.", "CARL M.", "YOUNGMAN") == (
        "YOUNGMAN, CARL M",
        "CARL M.",
        "YOUNGMAN",
    )
    assert read_name("OSHER, LOREN MARK", "LOREN", "OSHER") == (
        "OSHER, LOREN MARK",
        "LOREN MARK",
        "OSHER",
    )  # filed given names kept
    assert read_name("ELLIOTT FELSON, ELLIOTT FELSON", "ELLIOTT FELSON", "ELLIOTT FELSON") == (
        "FELSON, ELLIOTT",
        "ELLIOTT",
        "FELSON",
    )
    assert read_name("MOND, MRS", "MRS", "MOND") == (
        "MOND, MRS",
        "",
        "MOND",
    )  # a title is no first name
    assert read_name("DE TOLEDO, PHILIP", "PHILIP", "DE TOLEDO") == (
        "DE TOLEDO, PHILIP",
        "PHILIP",
        "DE TOLEDO",
    )
    assert read_name("CHALME'', RAYMOND", "RAYMOND", "CHALME''") == (
        "CHALME', RAYMOND",
        "RAYMOND",
        "CHALME'",
    )


def test_reviewed_filing_cells_replace_or_clear_and_wait_for_matching():
    from fec.cleaning import filing_corrections

    rows = filing_corrections._read_reviewed_filings()
    waiting = rows.index[rows["after_matching"].str.lower().eq("true") & rows["contributor_employer"].ne("")][0]
    cleared = rows.index[rows["contributor_employer"].str.upper().eq(filing_corrections.CLEAR)][0]
    df = pd.DataFrame({"sub_id": [waiting, cleared, "1"], "contributor_employer": ["FILED", "FILED", "FILED"]})
    for column in filing_corrections.FIELDS[1:]:
        df[column] = "FILED"
    filing_corrections.apply_filing_corrections(df)
    assert df["contributor_employer"].tolist()[0] == "FILED" and pd.isna(df["contributor_employer"][1])
    filing_corrections.apply_filing_corrections(df, matched=True)
    assert (
        df["contributor_employer"][0] == rows.at[waiting, "contributor_employer"]
        and df["contributor_employer"][2] == "FILED"
    )


def test_previous_employer_survives_a_later_not_employed_but_not_a_later_job():
    from fec.resolve.previous_employer import previous_employers

    filings = [
        # donor, date, status, employer: MAGIC, then retired, then not employed: retirement goes on
        ("epstein", "2024-02-10", "active", "MAGIC"),
        ("epstein", "2024-05-21", "retired", "RETIRED"),
        ("epstein", "2025-09-28", "not_employed", "NOT EMPLOYED"),
        # retired, then a new job: the later company is not a former one
        ("adler", "2022-03-21", "retired", "RETIRED"),
        ("adler", "2023-06-01", "active", "ACME"),
        ("adler", "2025-01-10", "not_employed", "NOT EMPLOYED"),
        # retired now, as before
        ("koum", "2021-01-01", "active", "WHATSAPP LLC"),
        ("koum", "2022-01-01", "retired", "RETIRED"),
    ]
    df = pd.DataFrame(
        {
            "sub_id": [str(number) for number in range(len(filings))],
            "donor_key": [filing[0] for filing in filings],
            "contribution_receipt_date": [filing[1] for filing in filings],
            "contributor_employer": [filing[3] for filing in filings],
            "previous_employer": [""] * len(filings),
            "entity_type": ["INDIVIDUAL"] * len(filings),
            "identity_status": ["confirmed"] * len(filings),
        }
    )
    status = pd.Series([filing[2] for filing in filings])
    found = previous_employers(df, status)
    assert found[1] == "MAGIC"
    assert found[3] == ""
    assert found[7] == "WHATSAPP LLC"
    assert (found[status.ne("retired")] == "").all()  # only retired filings carry one
