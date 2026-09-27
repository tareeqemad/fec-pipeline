"""A retired filing never takes a previous employer the donor only filed later (MARGOLIN, RUTH)."""
import pandas as pd

from fec.resolve.pipeline.apply import ResolveContext, _first_filed_employers, _resolve_retired

CLINIC = "ASSOCIATES IN GASTROENTEROLOGY OF UNION COUNTY"


def _retired(date):
    return pd.Series({"donor_key": "ruth", "contribution_receipt_date": date,
                      "contributor_name": "MARGOLIN, RUTH", "contributor_employer": "RETIRED"})


def _context(entry, first_filed=None):
    return ResolveContext(previous_cache={"donor:ruth": entry}, address_lookup={},
                          first_filed=first_filed or {})


def test_an_entry_sourced_after_the_filing_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous", "source_date": "2026-05-03"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_an_undated_entry_for_an_employer_first_filed_later_does_not_apply():
    entry = {"employer": CLINIC, "method": "cleaned_previous"}
    first = {("ruth", CLINIC): pd.Timestamp("2024-09-02")}
    assert _resolve_retired(_retired("2022-09-28"), _context(entry, first), "NJ", "07000")["previous_employer"] == ""
    assert _resolve_retired(_retired("2026-05-03"), _context(entry, first), "NJ", "07000")["previous_employer"] == CLINIC


def test_an_undated_entry_the_donor_never_filed_still_applies():
    entry = {"employer": CLINIC, "method": "cleaned_previous"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_a_hand_set_entry_always_applies():
    entry = {"employer": CLINIC, "method": "manual_override", "source_date": "2026-05-03"}
    assert _resolve_retired(_retired("2022-02-02"), _context(entry), "NJ", "07000")["previous_employer"] == CLINIC


def test_first_filed_employers_keeps_each_donors_earliest_date():
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * 3,
        "donor_key": ["ruth"] * 3,
        "contributor_employer": [f"{CLINIC} PA", f"{CLINIC} PA", "RETIRED"],
        "contribution_receipt_date": ["2025-01-01", "2024-09-02", "2022-02-02"],
    })
    assert _first_filed_employers(df)[("ruth", f"{CLINIC} PA")] == pd.Timestamp("2024-09-02")


FILED = {"s1": "UNIVERSITY OF CONNECTICUT"}


def _filed(previous, status="confirmed", key="stein", sub_id="s1"):
    return pd.Series({"sub_id": sub_id, "donor_key": key, "contribution_receipt_date": "2026-01-16",
                      "identity_status": status,
                      "contributor_name": "STEIN, ALAN", "contributor_employer": "RETIRED",
                      "previous_employer": previous})


def _ctx(cache=None, **extra):
    return ResolveContext(previous_cache=cache or {}, address_lookup={}, filed_employers=FILED, **extra)


def test_the_previous_employer_the_filing_wrote_is_kept():
    # filed UNIVERSITY OF CONNECTICUT / RETIRED: no cache entry, or a later-dated one, must not erase it
    uconn = "UNIVERSITY OF CONNECTICUT"
    assert _resolve_retired(_filed(uconn), _ctx(), "CT", "06000")["previous_employer"] == uconn
    later = {"donor:stein": {"employer": "YALE", "method": "cleaned_previous", "source_date": "2026-05-01"}}
    assert _resolve_retired(_filed(uconn), _ctx(later), "CT", "06000")["previous_employer"] == uconn


def test_a_value_from_other_filings_keeps_the_date_barrier():
    # SANDBERG: the clean stage filled it from an earlier filing; this filing wrote RETIRED
    entry = {"donor:stein": {"employer": "EYE SURGERY ASSOCIATES", "method": "cleaned_previous",
                             "source_date": "2026-05-01"}}
    row = _filed("EYE SURGERY ASSOCIATES", sub_id="s2")
    assert _resolve_retired(row, _ctx(entry), "FL", "33000")["previous_employer"] == ""


def test_a_hand_set_entry_or_clear_still_wins_over_the_filing():
    manual = {"donor:stein": {"employer": "JCRC", "method": "manual_override"}}
    assert _resolve_retired(_filed("UNIVERSITY OF CONNECTICUT"), _ctx(manual), "CT", "06000")["previous_employer"] == "JCRC"
    cleared = {"donor:stein": {"employer": "", "method": "cleared_swapped_record"}}
    assert _resolve_retired(_filed("UNIVERSITY OF CONNECTICUT"), _ctx(cleared), "CT", "06000")["previous_employer"] == ""


def test_a_held_filing_takes_nothing_from_its_group():
    cache = {"donor:HOLD|g": {"employer": "NIXON PEABODY", "method": "manual_override"}}
    context = _ctx(cache, dated_previous={0: "NIXON PEABODY"})
    assert _resolve_retired(_filed("", status="held", key="HOLD|g", sub_id="s9"), context, "PA", "19103", 0)[
        "previous_employer"] == ""
    assert _resolve_retired(_filed("UNIVERSITY OF CONNECTICUT", status="held", key="HOLD|g"), context, "PA", "19103", 0)[
        "previous_employer"] == "UNIVERSITY OF CONNECTICUT"


def test_a_request_for_information_is_not_a_previous_employer():
    cache = {"donor:stein": {"employer": "MORE INFO NEEDED", "method": "fec_api"}}
    assert _resolve_retired(_filed("", sub_id="s9"), _ctx(cache), "CA", "90210")["previous_employer"] == ""


def test_a_degree_or_pronoun_is_not_a_previous_employer():
    from fec.cleaning.previous_employer import normalize_previous_employer_value
    for value in ("MD", "ME", "M.D.", "MORE INFO NEEDED", "NOT EMPLYED"):
        assert normalize_previous_employer_value(value) == ""
    assert normalize_previous_employer_value("SELF-EMPLOYED") == "SELF-EMPLOYED"
    assert normalize_previous_employer_value("SSELF") == "SELF-EMPLOYED"  # SIMON, SHULAMITH


def test_an_own_named_firm_keeps_its_legal_form():
    row = _filed("AMY N DEAN PA", sub_id="d1")
    context = ResolveContext(previous_cache={}, address_lookup={}, filed_employers={"d1": "AMY N. DEAN P.A."})
    row["contributor_name"] = "DEAN, AMY"
    assert _resolve_retired(row, context, "FL", "33000")["previous_employer"] == "AMY N DEAN PA"
