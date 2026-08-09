"""previous_employer has one contract shared by every writer (post-merge steps and resolve)."""
import pandas as pd

from fec.cleaning.previous_employer import (
    normalize_previous_employer_value as V,
    normalize_previous_employer_column,
    referenced_employers,
)
from fec.cleaning.donor_consistency import _normalize_previous_employer
from fec.resolve.pipeline.quality_fixes import _normalize_previous_employer_column


def test_referenced_employers_are_individual_only():
    rows = pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL",
            "employer_status": "active",
            "contributor_employer": "REAL EMPLOYER",
            "previous_employer": "",
        },
        {
            "entity_type": "ORGANIZATION",
            "employer_status": "active",
            "contributor_employer": "SHOULD NOT LOAD",
            "previous_employer": "",
        },
    ])

    assert referenced_employers(rows) == {"REAL EMPLOYER"}


def test_self_employed_is_kept_not_cleared():
    """SELF-EMPLOYED is real information; empty means unknown."""
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


def test_curated_previous_employer_abbreviations_are_expanded():
    assert V("AMERICAN PSYCHOLOGICAL ASSOC.") == "AMERICAN PSYCHOLOGICAL ASSOCIATION"
    assert V("HARBOR CAPITAL MGMT LLC") == "HARBOR CAPITAL MANAGEMENT"
    assert V("NUCLEAR ONCOLOGY ASSOC.") == "NUCLEAR ONCOLOGY SC"
    assert V("SNOWS & ASSOC. LLP/CONSULTANT") == "SNOWS AND ASSOCIATES"
    assert V("TIGER MGMT") == "TIGER MANAGEMENT"
    assert V("SOUTH JERSEY PROSTHODONTIC ASSOC./O") == \
        "SOUTH JERSEY PROSTHODONTIC ASSOCIATES PA"


def test_slash_composites_are_collapsed():
    assert V("BRIDGESTONE/FIRESTONE") == "BRIDGESTONE/FIRESTONE"  # real brand
    assert V("RETIRED/ACME WIDGETS") == "ACME WIDGETS"            # status/company
    assert V("ACME WIDGETS/PRESIDENT") == "ACME WIDGETS"          # company/title
    assert V("LETTER SENT: ACME/PRESIDENT") == ""                 # admin note
    # The admin-prefix guard lives inside the slash branch; a slash-less admin note survives as a name.
    assert V("LETTER SENT: ACME") == "LETTER SENT: ACME"
    # Quirk kept: the slash branch treats a SELF side as no prior company and clears before the marker applies.
    assert V("SELF") == "SELF-EMPLOYED"
    assert V("SELF/CONSULTANT") == ""


def test_slash_result_still_passes_through_the_synonym_map():
    """The surviving side still goes through the synonym map."""
    assert V("RETIRED/IBM") == "IBM CORP"


def test_own_name_is_dropped():
    """A donor's previous employer is never the donor, in either name order."""
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


def test_own_named_legal_company_is_kept():
    """A legal suffix distinguishes a real solo practice from a bare name."""
    df = pd.DataFrame({
        "contributor_name": ["REINSTEIN, JOEL", "DRESNER, LINDA"],
        "previous_employer": ["JOEL REINSTEIN, PLLC", "LINDA DRESNER, INC."],
    })

    normalize_previous_employer_column(df)

    assert df["previous_employer"].tolist() == [
        "JOEL REINSTEIN PLLC",
        "LINDA DRESNER INC",
    ]


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


def _resolved_retiree(
    previous_employer, cached_employer, employer_source="",
    contributor_name="DOE, JANE", address_cache=None,
):
    """Run the resolve apply stage on one already-built retired donor."""
    from fec.resolve.pipeline.apply import apply_results

    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"],
        "contributor_name": [contributor_name],
        "contributor_employer": ["RETIRED"],
        "contributor_occupation": ["RETIRED"],
        "occupation_category": ["RETIRED"],
        "contributor_state": ["CA"],
        "contributor_street_1": [""],
        "contributor_city": ["LOS ANGELES"],
        "contributor_zip": ["90001"],
        "previous_employer": [previous_employer],
    })
    prev_cache = {
        f"{contributor_name}|CA": {
            "employer": cached_employer,
            "employer_normalized": cached_employer,
            "employer_source": employer_source,
            "method": "cross_record",
        }
    }
    return apply_results(df, prev_cache, address_cache or {}, {})


def test_resolve_keeps_build_owned_display_for_the_same_company():
    """A tail rerun must not toggle APPLE INC -> APPLE -> APPLE INC."""
    first = _resolved_retiree("APPLE INC", "APPLE INC")
    assert first["previous_employer"].iloc[0] == "APPLE INC"

    second = _resolved_retiree(first["previous_employer"].iloc[0], "APPLE INC")
    assert second["previous_employer"].iloc[0] == "APPLE INC"


def test_resolve_still_applies_a_real_previous_employer_correction():
    """Display stability must never freeze a different cached company."""
    resolved = _resolved_retiree("APPLE INC", "MICROSOFT CORP")
    assert resolved["previous_employer"].iloc[0] == "MICROSOFT"


def test_resolve_keeps_self_employed_as_known_work_history():
    """Self-employment is known history even though it has no company address."""
    first = _resolved_retiree("", "SELF-EMPLOYED")
    assert first["previous_employer"].iloc[0] == "SELF-EMPLOYED"

    second = _resolved_retiree(first["previous_employer"].iloc[0], "SELF-EMPLOYED")
    assert second["previous_employer"].iloc[0] == "SELF-EMPLOYED"


def test_resolve_keeps_own_named_legal_company_and_is_idempotent():
    first = _resolved_retiree(
        "", "JOEL REINSTEIN PLLC", "JOEL REINSTEIN, PLLC",
        "REINSTEIN, JOEL",
        {
            "JOEL REINSTEIN": {"employer_address": "RESIDENCE"},
            "JOEL REINSTEIN PLLC": {"employer_address": "OFFICE"},
        },
    )
    first_name = first["previous_employer"].iloc[0]
    assert first_name == "JOEL REINSTEIN PLLC"
    assert first["employer_address"].iloc[0] == "OFFICE"

    second = _resolved_retiree(
        first_name, "JOEL REINSTEIN PLLC", "JOEL REINSTEIN, PLLC",
        "REINSTEIN, JOEL",
    )
    assert second["previous_employer"].iloc[0] == first_name


def test_fec_previous_employer_requires_matching_city_or_zip():
    from fec.resolve.pipeline.steps.previous_employer import _same_fec_donor

    person = {
        "name": "JOHNSON, JAMES",
        "state": "CA",
        "city": "GREENBRAE",
        "zip": "94904",
    }
    same_person = {
        "contributor_name": "JOHNSON, JAMES M",
        "contributor_state": "CA",
        "contributor_city": "GREENBRAE",
        "contributor_zip": "94904-1234",
    }
    different_person = {
        **same_person,
        "contributor_city": "LOS ANGELES",
        "contributor_zip": "90069",
    }

    assert _same_fec_donor(person, same_person)
    assert not _same_fec_donor(person, different_person)


def test_fec_previous_employer_rejects_conflicting_middle_initials():
    from fec.resolve.pipeline.steps.previous_employer import _same_fec_donor

    person = {
        "name": "JOHNSON, JAMES R",
        "state": "CA",
        "city": "GREENBRAE",
        "zip": "94904",
    }
    record = {
        "contributor_name": "JOHNSON, JAMES M",
        "contributor_state": "CA",
        "contributor_city": "GREENBRAE",
        "contributor_zip": "94904",
    }

    assert not _same_fec_donor(person, record)
