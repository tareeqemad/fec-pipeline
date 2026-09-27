"""Each filing keeps its own job title: nothing rewrites it to another filing's title."""
from fec.cleaning.donor_consistency.steps import CONSISTENCY_FIXES
from fec.config.occupation_rules.normalize import OCCUPATION_NORMALIZE


def test_no_donor_step_rewrites_a_filed_title_to_another_one():
    # CEO -> CFO, MD, SCIENTIST, EXECUTIVE -> EXECUTIVE came from one such step
    names = {label for label, *_ in CONSISTENCY_FIXES}
    assert "converge occupation within employer" not in names


def test_an_abbreviation_with_more_than_one_reading_stays_as_filed():
    for title in ("CIO", "CMO", "CDO"):
        assert title not in OCCUPATION_NORMALIZE
    assert OCCUPATION_NORMALIZE["CHIEF EXECUTIVE OFFICER"] == "CEO"
