"""Employer-name hygiene: legal-suffix restyle, canonical key, display name."""
import pandas as pd

from fec.cleaning.employer_synonyms import (
    canonical_key,
    finalize_employer_names,
    normalize_employer_display_name,
    restyle_legal_suffix,
)


def test_suffix_comma_restyle():
    assert restyle_legal_suffix('510 VENTURES, LLC') == '510 VENTURES LLC'
    assert restyle_legal_suffix('ACCOUNTNET, INC') == 'ACCOUNTNET INC'
    assert restyle_legal_suffix('ABROMS & ASSOCIATES, PC') == 'ABROMS & ASSOCIATES PC'


def test_suffix_restyle_tolerates_filer_punctuation():
    # the trailing period is stripped by a later step, so the comma must match while present
    assert restyle_legal_suffix('T & M BUILDING CO,, INC.') == 'T & M BUILDING CO INC.'
    assert restyle_legal_suffix('T & M BUILDING CO, INC') == 'T & M BUILDING CO INC'


def test_co_period_before_a_suffix_is_dropped_but_initials_keep_theirs():
    assert restyle_legal_suffix('AMERICAL MANAGEMENT CO., INC') == 'AMERICAL MANAGEMENT CO INC'
    assert restyle_legal_suffix('AMERICAL MANAGEMENT CO. INC') == 'AMERICAL MANAGEMENT CO INC'
    assert restyle_legal_suffix('L.M. COHEN & CO. LLP') == 'L.M. COHEN & CO LLP'
    assert restyle_legal_suffix('CARYN GROEDEL & ASSOCIATES CO., LPA') == 'CARYN GROEDEL & ASSOCIATES CO LPA'
    assert restyle_legal_suffix('TIFFANY & CO.') == 'TIFFANY & CO'
    assert restyle_legal_suffix('R.A. COHEN & ASSOCIATES INC') == 'R.A. COHEN & ASSOCIATES INC'
    assert restyle_legal_suffix('U.S. DEPARTMENT OF STATE') == 'U.S. DEPARTMENT OF STATE'
    assert restyle_legal_suffix('CO. FOUNDERS FUND') == 'CO. FOUNDERS FUND'   # not a suffix position


def test_suffix_dedot_ordering_and_exception():
    assert restyle_legal_suffix('KUSTOFF AND SANDERS L.L.P') == 'KUSTOFF AND SANDERS LLP'
    assert restyle_legal_suffix('GERSON & SCHWARTZ, P.A') == 'GERSON & SCHWARTZ PA'
    # trailing period (what filers actually type) must still de-dot
    assert restyle_legal_suffix('BURKE, WARREN, MACKAY & SERRITELLA P.C.') == \
        'BURKE, WARREN, MACKAY & SERRITELLA PC'
    assert restyle_legal_suffix('CORNERSTONE PSYCHIATRY ASSOCIATES, P.A') == \
        'CORNERSTONE PSYCHIATRY ASSOCIATES PA'
    # real company a.l.p. Lighting, not a limited partnership
    assert restyle_legal_suffix('A.L.P') == 'A.L.P'
    assert restyle_legal_suffix('A.L.P.') == 'A.L.P.'


def test_restyle_never_strips_suffix():
    # law-firm partner commas before the suffix position are untouched
    assert restyle_legal_suffix('BENESCH, FRIEDLANDER, COPLAN & ARONOFF LLP') == \
        'BENESCH, FRIEDLANDER, COPLAN & ARONOFF LLP'


def test_canonical_key_incorporated_and_tokens():
    assert canonical_key('MOVIL INCORPORATED') == canonical_key('MOVIL INC')
    assert canonical_key('UNIV OF CHICAGO') == canonical_key('UNIVERSITY OF CHICAGO')
    assert canonical_key('MT SINAI HOSPITAL') == canonical_key('MOUNT SINAI HOSPITAL')
    # ASSOC deliberately NOT folded (ASSOCIATES vs ASSOCIATION is contextual)
    assert canonical_key('RADIOLOGY ASSOC') != canonical_key('RADIOLOGY ASSOCIATES')


def test_final_employer_pass_collapses_late_variants():
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL", "INDIVIDUAL"],
        "contributor_employer": ["ACME MGMT LLC", "ACME MANAGEMENT"],
        "previous_employer": ["", ""],
    })

    finalize_employer_names(df)

    assert df["contributor_employer"].nunique() == 1
    assert df["contributor_employer"].iloc[0] == "ACME MANAGEMENT LLC"


def test_current_employer_spelling_wins_over_previous_history():
    df = pd.DataFrame({
        "entity_type": ["INDIVIDUAL"] * 3,
        "contributor_employer": ["ACME LLC", "", ""],
        "previous_employer": ["", "ACME", "ACME"],
    })

    finalize_employer_names(df)

    assert df["contributor_employer"].iloc[0] == "ACME LLC"
    assert set(df.loc[1:, "previous_employer"]) == {"ACME LLC"}


def test_display_name_trailing_connectors_and_co():
    assert normalize_employer_display_name('C/O SCHUWARGER & ASSOCIATES') == \
        'SCHUWARGER & ASSOCIATES'
    assert normalize_employer_display_name('SOUTHERN GLAZERS WINE AND') == \
        'SOUTHERN GLAZERS WINE'


def test_display_name_converges_on_synonyms():
    # previous_employer path must land on the curated canonical
    assert normalize_employer_display_name('IBM') == 'IBM CORP'


def test_new_employer_variants_use_known_companies():
    expected = {
        'CHIEFTAIN CAPITAL MANAGEMENT INC': 'CHIEFTAIN CAPITAL MANAGEMENT',
        'ELLIOTT INVESTMENT MANAGEMENT': 'ELLIOTT INVESTMENT MANAGEMENT LP',
        'PIERPOINT CAPITAL MANAGEMENT': 'PIERPOINT',
        'FISHMAN, BLOCK, DIAMOND, BY CERITY PAR':
            'FISHMAN BLOCK + DIAMOND BY CERITY PARTNERS',
    }
    for variant, canonical in expected.items():
        assert normalize_employer_display_name(variant) == canonical


def test_jll_partners_is_not_jones_lang_lasalle():
    assert normalize_employer_display_name('JONES LANG LASALLE') == 'JLL'
    assert normalize_employer_display_name('JLL PARTNERS') == 'JLL PARTNERS'


def test_maven_ventures_is_not_collapsed_to_maven():
    assert normalize_employer_display_name('MAVEN VENTURES') == 'MAVEN VENTURES'


def test_display_name_retired_prefix():
    assert normalize_employer_display_name('RETIRED - ORTHOCAROLINA') == 'ORTHOCAROLINA'
    assert normalize_employer_display_name('RETIRED US ARMY') == 'US ARMY'
    assert normalize_employer_display_name('RETIRED PHYSICIAN') is None
    assert normalize_employer_display_name('RETIRED MILITARY') is None
