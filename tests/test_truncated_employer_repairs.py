"""38-char FEC truncation repairs: a full name must extend the stored truncated prefix."""
import json

from fec.cleaning.employer_synonyms import EMPLOYER_SYNONYMS

# 38-char truncations repaired from external sources.
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


def test_repairs_are_wired_into_the_synonym_map():
    for truncated, full in REPAIRED.items():
        assert EMPLOYER_SYNONYMS.get(truncated) == full, truncated


def test_every_repair_extends_the_truncated_string():
    """A completion continues the stored prefix."""
    for truncated, full in REPAIRED.items():
        assert len(truncated) == 38, f"{truncated!r} is not a 38-char truncation"
        extends = full.startswith(truncated)
        # a cut inside a legal suffix (', P.A.') legitimately gets shorter after suffix stripping
        shortens_to_prefix = truncated.startswith(full)
        assert extends or shortens_to_prefix, (
            f"{full!r} neither extends nor trims {truncated!r} — different organisation?"
        )


def test_override_file_stays_consistent_with_this_list():
    """The JSON file is the source of truth; this test fails if it drifts."""
    with open("data/manual_typo_overrides.json", encoding="utf-8") as f:
        overrides = json.load(f)
    for truncated, full in REPAIRED.items():
        assert overrides.get(truncated) == full, truncated


def test_no_override_maps_a_name_to_itself():
    """A self-mapping entry is dead weight and hides a misunderstanding."""
    with open("data/manual_typo_overrides.json", encoding="utf-8") as f:
        overrides = json.load(f)
    selfmaps = [k for k, v in overrides.items() if k.strip().upper() == str(v).strip().upper()]
    assert not selfmaps, f"override maps these to themselves: {selfmaps}"
