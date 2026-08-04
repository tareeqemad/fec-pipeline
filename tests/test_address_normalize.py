"""_normalize_street regression tests: street-type preservation."""
from fec.cleaning.addresses import _normalize_street as norm


def test_trailing_street_type_with_period_is_kept():
    # the stray-state-code cleanup must not eat 2-letter street types ending in a period
    assert norm("445 S. FIGUEROA ST.") == "445 S FIGUEROA ST"
    assert norm("301 W. PLATT ST.") == "301 W PLATT ST"
    assert norm("77 MANDARIN RD.") == "77 MANDARIN RD"
    assert norm("12 OSPREY LN.") == "12 OSPREY LN"
    assert norm("5 BIRCH CT.") == "5 BIRCH CT"


def test_period_free_types_unchanged():
    assert norm("123 MAIN ST") == "123 MAIN ST"
    assert norm("99 ELM AVE") == "99 ELM AVE"


def test_bare_box_becomes_po_box():
    # Committees often file "BOX 137", a PO box with the "PO" dropped.
    assert norm("BOX 137") == "PO BOX 137"
    assert norm("BOX 5") == "PO BOX 5"
    # A street NAME starting with BOX (no digits after) is untouched.
    assert norm("BOX CANYON RD") == "BOX CANYON RD"
    # Already-correct forms unchanged.
    assert norm("PO BOX 137") == "PO BOX 137"


def test_verified_street_typo_is_corrected():
    assert norm("11301 W. OLYMIC BLVD") == "11301 W OLYMPIC BLVD"
