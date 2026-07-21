"""New employer-name hygiene rules from the 2026-07-20 audit."""
import pandas as pd

from fec.cleaning.employer_synonyms import (
    restyle_legal_suffix, canonical_key, normalize_employer_display_name,
)


def test_suffix_comma_restyle():
    assert restyle_legal_suffix('510 VENTURES, LLC') == '510 VENTURES LLC'
    assert restyle_legal_suffix('ACCOUNTNET, INC') == 'ACCOUNTNET INC'
    assert restyle_legal_suffix('ABROMS & ASSOCIATES, PC') == 'ABROMS & ASSOCIATES PC'


def test_suffix_restyle_tolerates_filer_punctuation():
    # raw FEC value: "T & M BUILDING CO,, INC." — the trailing period is
    # stripped by a later step, so the comma must match while it is present
    assert restyle_legal_suffix('T & M BUILDING CO,, INC.') == 'T & M BUILDING CO INC.'
    assert restyle_legal_suffix('T & M BUILDING CO, INC') == 'T & M BUILDING CO INC'


def test_suffix_dedot_ordering_and_exception():
    assert restyle_legal_suffix('KUSTOFF AND SANDERS L.L.P') == 'KUSTOFF AND SANDERS LLP'
    assert restyle_legal_suffix('GERSON & SCHWARTZ, P.A') == 'GERSON & SCHWARTZ PA'
    # trailing period (what filers actually type) must still de-dot
    assert restyle_legal_suffix('BURKE, WARREN, MACKAY & SERRITELLA P.C.') == \
        'BURKE, WARREN, MACKAY & SERRITELLA PC'
    assert restyle_legal_suffix('CORNERSTONE PSYCHIATRY ASSOCIATES, P.A') == \
        'CORNERSTONE PSYCHIATRY ASSOCIATES PA'
    # real company a.l.p. Lighting — not a limited partnership
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


def test_display_name_trailing_connectors_and_co():
    assert normalize_employer_display_name('C/O SCHUWARGER & ASSOCIATES') == \
        'SCHUWARGER & ASSOCIATES'
    assert normalize_employer_display_name('SOUTHERN GLAZERS WINE AND') == \
        'SOUTHERN GLAZERS WINE'


def test_display_name_converges_on_synonyms():
    # previous_employer path must land on the curated canonical (IBM split fix)
    assert normalize_employer_display_name('IBM') == 'IBM CORP'


def test_display_name_retired_prefix():
    assert normalize_employer_display_name('RETIRED - ORTHOCAROLINA') == 'ORTHOCAROLINA'
    assert normalize_employer_display_name('RETIRED US ARMY') == 'US ARMY'
    assert normalize_employer_display_name('RETIRED PHYSICIAN') is None
    assert normalize_employer_display_name('RETIRED MILITARY') is None
