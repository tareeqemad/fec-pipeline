"""Accepted and rejected 38-char FEC truncation repairs."""
import csv

from fec.cleaning.work.employer_aliases import EMPLOYER_SYNONYMS
from fec.cleaning.work.employer_normalize import normalize_employer_display_name
from fec.env import EMPLOYER_NAME_RULES_CSV

# Original proposals remain covered, including those the source review rejected.
REPAIRED = {
    "THE RENFREW CENTER FOR EATING DISORDER": "THE RENFREW CENTER FOR EATING DISORDERS",
    "NORTHERN VALLEY MEDICAL ASSOCIATION, P": "NORTHERN VALLEY MEDICAL ASSOCIATION",
    "NATIONWIDE CREDIT CORPORATION ALEXANDR": "NATIONWIDE CREDIT CORPORATION ALEXANDRIA VA",
    "STANHOPE FINANCIAL GROUP HOLDINGS LIMI": "STANHOPE FINANCIAL GROUP HOLDINGS LIMITED",
    "JEWISH FEDERATION OF GREATER FAIRFIELD": "JEWISH FEDERATION OF GREATER FAIRFIELD COUNTY",
    "HOLLISWOOD DEVELOPMENT / EDIFICE MANAG": "HOLLISWOOD DEVELOPMENT / EDIFICE MANAGEMENT",
    "EAR NOSE AND THROAT SURGEONS OF WESTER": "EAR NOSE AND THROAT SURGEONS OF WESTERN NEW ENGLAND",
    "CARROLL COUNTY EMERGENCY MANAGEMENT AN": "CARROLL COUNTY EMERGENCY MANAGEMENT AND COMMUNICATIONS",
    "CARDIOLOGY PC OF HARTFORD HEALTHCARE M": "CARDIOLOGY PC OF HARTFORD HEALTHCARE MEDICAL GROUP",
    "DENTAL IMPLANTS AND PERIODONTAL HEALTH": "DENTAL IMPLANTS AND PERIODONTAL HEALTH OF ROCHESTER",
    "SPOTTSWOOD SPOTTSWOOD SPOTTSWOOD & STE": "SPOTTSWOOD SPOTTSWOOD SPOTTSWOOD & STERLING",
}

PRESERVED = {
    "NORTHERN VALLEY MEDICAL ASSOCIATION, P",  # do not discard an unknown legal suffix
    "CARDIOLOGY PC OF HARTFORD HEALTHCARE M",  # official name does not confirm this completion
    "DENTAL IMPLANTS AND PERIODONTAL HEALTH",  # Rochester cannot be inferred globally
    "NATIONWIDE CREDIT CORPORATION ALEXANDR",  # no documented global completion
    "HOLLISWOOD DEVELOPMENT / EDIFICE MANAG",  # no documented global completion
}


def test_repairs_are_wired_into_the_synonym_map():
    for truncated, full in REPAIRED.items():
        if truncated in PRESERVED:
            assert truncated not in EMPLOYER_SYNONYMS
            expected = "NORTHERN VALLEY MEDICAL ASSOCIATION P" if truncated == "NORTHERN VALLEY MEDICAL ASSOCIATION, P" else truncated
            assert normalize_employer_display_name(truncated) == expected
            assert normalize_employer_display_name(truncated) != full
        else:
            assert EMPLOYER_SYNONYMS.get(truncated) == full, truncated


def test_every_repair_extends_the_truncated_string():
    """Every stored proposal is a completion or a rejected suffix deletion."""
    for truncated, full in REPAIRED.items():
        assert len(truncated) == 38, f"{truncated!r} is not a 38-char truncation"
        extends = full.startswith(truncated)
        # The rejected Northern Valley proposal deleted an incomplete legal suffix.
        shortens_to_prefix = truncated.startswith(full)
        assert extends or shortens_to_prefix, (
            f"{full!r} neither extends nor trims {truncated!r} — different organisation?"
        )


def test_rules_file_stays_consistent_with_this_list():
    """The CSV is authoritative."""
    with EMPLOYER_NAME_RULES_CSV.open(encoding="utf-8", newline="") as handle:
        overrides = {
            row["variant"]: row
            for row in csv.DictReader(handle)
        }
    for truncated, full in REPAIRED.items():
        assert overrides[truncated]["canonical"] == full, truncated
        assert overrides[truncated]["action"] == (
            "separate" if truncated in PRESERVED else "rename"
        )


def test_no_override_maps_a_name_to_itself():
    """A self-mapping entry is dead weight and hides a misunderstanding."""
    with EMPLOYER_NAME_RULES_CSV.open(encoding="utf-8", newline="") as handle:
        overrides = {
            row["variant"]: row["canonical"]
            for row in csv.DictReader(handle)
        }
    selfmaps = [k for k, v in overrides.items() if k.strip().upper() == str(v).strip().upper()]
    assert not selfmaps, f"override maps these to themselves: {selfmaps}"
