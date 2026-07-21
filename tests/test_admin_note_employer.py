"""tests/test_admin_note_employer.py — the admin-note/refusal employer net nulls
FEC placeholder phrasings (new wording included) WITHOUT touching a real company
name that merely contains such a word."""
import pandas as pd

from fec.cleaning.safety_nets.employer import _clear_admin_note_employers


def test_admin_notes_nulled_real_companies_kept():
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * 9,
        "contributor_employer": [
            "PER BEST EFFORTS", "REQUEST SENT", "INFO REQ", "DECLINED",
            "PREFER NOT TO DISCLOSE", "PENDING", "NO RESPONSE",
            # real companies that CONTAIN an admin-note word -> must be kept
            "REQUEST FOODS INC", "PENDING SYSTEMS LLC",
        ],
        "occupation_status": ["DISCLOSED"] * 9,
    })
    is_indiv = df["entity_type"] == "INDIVIDUAL"

    n = _clear_admin_note_employers(df, is_indiv)

    emp = df["contributor_employer"]
    assert emp.iloc[:7].isna().all()                 # 7 admin-notes nulled
    assert emp.iloc[7] == "REQUEST FOODS INC"        # real company kept
    assert emp.iloc[8] == "PENDING SYSTEMS LLC"      # real company kept
    assert n == 7


def test_admin_note_net_skips_non_individuals():
    df = pd.DataFrame({
        "entity_type": ["COMMITTEE/PAC"],
        "contributor_employer": ["DECLINED"],
        "occupation_status": ["NOT_APPLICABLE"],
    })
    is_indiv = df["entity_type"] == "INDIVIDUAL"
    n = _clear_admin_note_employers(df, is_indiv)
    assert df["contributor_employer"].iloc[0] == "DECLINED"   # committee untouched
    assert n == 0
