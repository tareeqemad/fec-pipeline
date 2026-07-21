"""tests/test_swap_role_known_company.py — AD0, the cross-record swap net.

A filer who put the job title in the employer field and the COMPANY in the
occupation field used to lose the company: AD collapsed the title to
SELF-EMPLOYED and its own swap-back only fires when _COMPANY_NAME_RE recognises
the occupation — a detector that misses bare brand names.

AD0 decides using the DATA, not the shape of the string: the occupation is
treated as a company only when other donors already use that exact string as
their employer."""
import numpy as np
import pandas as pd

from fec.cleaning.safety_nets.employer import _swap_role_employer_with_known_company


def _frame(rows):
    """rows = (employer, occupation)"""
    return pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * len(rows),
        "contributor_employer": [r[0] for r in rows],
        "contributor_occupation": [r[1] for r in rows],
        "occupation_status": ["DISCLOSED"] * len(rows),
    })


def test_swaps_when_other_donors_use_the_occupation_as_an_employer():
    df = _frame([
        ("CHAIRMAN", "KIMCO REALTY"),      # the swapped row
        ("KIMCO REALTY", "ANALYST"),       # another donor — the corroborating evidence
    ])

    assert _swap_role_employer_with_known_company(df) == 1
    assert df.loc[0, "contributor_employer"] == "KIMCO REALTY"
    assert df.loc[0, "contributor_occupation"] == "CHAIRMAN"
    assert df.loc[1, "contributor_employer"] == "KIMCO REALTY"   # untouched


def test_no_swap_without_corroboration():
    """No other donor names that company, so nothing justifies the swap —
    the row is left for AD to resolve as SELF-EMPLOYED."""
    df = _frame([("CHAIRMAN", "SOME UNVERIFIABLE THING")])

    assert _swap_role_employer_with_known_company(df) == 0
    assert df.loc[0, "contributor_employer"] == "CHAIRMAN"


def test_a_title_cannot_vouch_for_itself():
    """Two filers making the SAME mistake must not corroborate each other."""
    df = _frame([
        ("PRESIDENT", "CHAIRMAN"),
        ("CHAIRMAN", "PRESIDENT"),
    ])

    assert _swap_role_employer_with_known_company(df) == 0


def test_status_words_do_not_corroborate():
    df = _frame([
        ("CHAIRMAN", "SELF-EMPLOYED"),
        ("SELF-EMPLOYED", "CONSULTANT"),
    ])

    assert _swap_role_employer_with_known_company(df) == 0
    assert df.loc[0, "contributor_employer"] == "CHAIRMAN"


def test_real_employers_are_never_touched():
    df = _frame([("GOLDMAN SACHS", "CHAIRMAN"), ("ACME WIDGETS", "PRESIDENT")])
    before = df["contributor_employer"].tolist()

    assert _swap_role_employer_with_known_company(df) == 0
    assert df["contributor_employer"].tolist() == before


def test_committees_are_skipped():
    df = _frame([("CHAIRMAN", "KIMCO REALTY"), ("KIMCO REALTY", "ANALYST")])
    df.loc[0, "entity_type"] = "COMMITTEE/PAC"

    assert _swap_role_employer_with_known_company(df) == 0


def test_runs_before_the_role_net_in_the_registry():
    """Ordering is the whole point — AD would otherwise strand the company."""
    from fec.cleaning.safety_nets import _SAFETY_NETS
    from fec.cleaning.safety_nets.employer import _fix_role_as_employer

    names = [fn for fn, _ in _SAFETY_NETS]
    assert names.index(_swap_role_employer_with_known_company) < names.index(_fix_role_as_employer)
