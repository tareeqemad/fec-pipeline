"""tests/test_previous_employer_contract.py — previous_employer has ONE contract.

The column is written by three post-merge steps and by the resolve stage. Before
this contract was extracted, each writer decided for itself what counted as a
company, so the column's contents depended on which stage ran last."""
import pandas as pd

from fec.cleaning.previous_employer import (
    normalize_previous_employer_value as V,
    normalize_previous_employer_column,
)
from fec.database.post_merge_fixes import _normalize_previous_employer
from fec.resolve.pipeline.apply import _normalize_previous_employer_column


def test_self_employed_is_kept_not_cleared():
    """The fact a retiree worked for THEMSELVES is real information.

    An empty column means "we don't know what they did"; 'SELF-EMPLOYED' means
    "we know: not at a company". Clearing it would turn knowledge into ignorance.
    """
    assert V("SELF-EMPLOYED") == "SELF-EMPLOYED"
    assert V("SELF EMPLOYED") == "SELF-EMPLOYED"
    assert V("SELF") == "SELF-EMPLOYED"
    assert V("SELF: CONSULTING") == "SELF-EMPLOYED"
    # a bare job title means the same thing
    assert V("ATTORNEY") == "SELF-EMPLOYED"


def test_non_companies_are_cleared():
    for junk in ("RETIRED", "HOMEMAKER", "NOT EMPLOYED", "NOTEMPLOYED",
                 "STUDENT", "VOLUNTEER", "N/A", "REAL ESTATE", "HEALTHCARE",
                 "someone@example.com", "XXN"):
        assert V(junk) == "", junk


def test_real_companies_are_normalized():
    assert V("MARCUM LLP") == "MARCUM"
    assert V("SIDLEY AUSTIN LLP") == "SIDLEY AUSTIN"
    assert V("CHAPEL HAVEN") == "CHAPEL HAVEN"


def test_slash_composites_are_collapsed():
    assert V("BRIDGESTONE/FIRESTONE") == "BRIDGESTONE/FIRESTONE"  # real brand
    assert V("RETIRED/ACME WIDGETS") == "ACME WIDGETS"            # status/company
    assert V("ACME WIDGETS/PRESIDENT") == "ACME WIDGETS"          # company/title
    assert V("LETTER SENT: ACME/PRESIDENT") == ""                 # admin note
    # The admin-prefix guard lives INSIDE the slash branch, so a slash-less
    # admin note is not caught here — it survives as a name. Recorded as the
    # current behaviour, not endorsed as correct.
    assert V("LETTER SENT: ACME") == "LETTER SENT: ACME"
    # Quirk, documented rather than changed: a bare "SELF" yields SELF-EMPLOYED,
    # but "SELF/<anything>" clears instead. The slash branch treats a SELF side
    # as "no prior company" and returns '' before the self-employment marker can
    # apply. Pre-existing behaviour — preserved byte-for-byte by the extraction.
    assert V("SELF") == "SELF-EMPLOYED"
    assert V("SELF/CONSULTANT") == ""


def test_slash_result_still_passes_through_the_synonym_map():
    """Collapsing the slash is not the last word — the surviving side is still
    unified to the canonical company name, same as contributor_employer."""
    assert V("RETIRED/IBM") == "IBM CORP"


def test_own_name_is_dropped():
    """A donor's previous employer is never the donor — order-insensitive."""
    df = pd.DataFrame({
        "contributor_name": ["HOWARD, ALIDA", "COMITER, RICHARD", "ROSEN, MARVIN"],
        "previous_employer": [
            "ALIDA HOWARD",                      # own name reversed -> cleared
            "COMITER, SINGER, BASEMAN & BRAUN",  # shared surname -> kept
            "MARVIN S ROSEN",                    # own name + middle initial -> cleared
        ],
    })

    normalize_previous_employer_column(df)

    assert df["previous_employer"].iloc[0] == ""
    assert df["previous_employer"].iloc[1] != ""
    assert df["previous_employer"].iloc[2] == ""


def test_both_stages_agree():
    """The whole point of extracting the contract."""
    values = ["SELF-EMPLOYED", "MARCUM LLP", "RETIRED", "DAVIS POLK & WARDWELL LLP",
              "CHAPEL HAVEN", "HOMEMAKER", "RETIRED/IBM"]

    cleaning = pd.DataFrame({"previous_employer": list(values)})
    _normalize_previous_employer(cleaning)

    resolve = pd.DataFrame({"previous_employer": list(values)})
    _normalize_previous_employer_column(resolve)

    assert cleaning["previous_employer"].tolist() == resolve["previous_employer"].tolist()
    # and SELF-EMPLOYED survived both
    assert cleaning["previous_employer"].iloc[0] == "SELF-EMPLOYED"


def test_idempotent():
    """A second clean.py run must report zero fixes, not churn."""
    df = pd.DataFrame({"previous_employer": ["MARCUM LLP", "SELF-EMPLOYED", "RETIRED"]})

    _normalize_previous_employer(df)
    settled = df["previous_employer"].tolist()

    assert _normalize_previous_employer(df) == 0
    assert df["previous_employer"].tolist() == settled


def test_missing_column_is_not_an_error():
    assert _normalize_previous_employer(pd.DataFrame({"contributor_employer": ["ACME"]})) == 0
