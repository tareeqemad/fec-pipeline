"""Employer rule cases retained through the 2026-10-23 source review."""
import csv

import pandas as pd
import pytest

from fec.cleaning.work.employer_names import (
    apply_employer_synonyms,
    finalize_employer_names,
)
from fec.cleaning.work.employer_normalize import normalize_employer_display_name
from fec.cleaning.work.employer_aliases import EMPLOYER_SYNONYMS
from fec.config.not_employers import LEGAL_SUFFIX_RE
from fec.env import EMPLOYER_NAME_RULES_CSV

# Original candidate pairs; rejected proposals remain exercised below.
VERIFIED = {
    'AB INITIO': 'AB INITIO SOFTWARE',            # Ab Initio Software LLC, 201 Spring St, Lexington MA
    'ABINITIO': 'AB INITIO SOFTWARE',
    'ABINITIO SOFTWARE': 'AB INITIO SOFTWARE',
    'SPGI': 'S&P GLOBAL',                         # NYSE ticker of S&P Global Inc.
    'NMRK': 'NEWMARK GROUP',                      # Nasdaq ticker of Newmark Group Inc.
    'JPMC': 'JPMORGAN CHASE',                     # collective brand can cover distinct affiliates
    'C21 STORES': 'CENTURY 21 STORES',            # the Gindi family's store, c21stores.com
    'C21STORES': 'CENTURY 21 STORES',
    'KW REALTY': 'KELLER WILLIAMS',               # local offices do not establish a franchisor employer
    'TWINCITY FAN': 'TWIN CITY FAN COMPANIES',    # Twin City Fan Companies Ltd, Minneapolis
    'TWIN CITY FAN': 'TWIN CITY FAN COMPANIES',
    'MILE ONE': 'MILEONE AUTOGROUP',              # MileOne Autogroup, Towson MD
    'WARNER BROTHERS': 'WARNER BROS',
    'ARMY': 'US ARMY',
    'DEPT OF ARMY': 'US ARMY',
    'ADL': 'ANTI-DEFAMATION LEAGUE',
    'FACEBOOK': 'META',                           # preserve the filed historical/product name
    'DWT': 'DAVIS WRIGHT TREMAINE',               # dwt.com
    'U.C. REGENTS': 'UNIVERSITY OF CALIFORNIA',   # The Regents = UC's corporate name
    'UC REGENTS': 'UNIVERSITY OF CALIFORNIA',
    'MILLENNIUM CAPITAL MANAGEMENT': 'MILLENNIUM MANAGEMENT',
    'MILLENNIUM CAPITAL': 'MILLENNIUM MANAGEMENT',
    'VETERANS ADMINISTRATION': 'DEPT OF VETERANS AFFAIRS',  # historical name is preserved
    'US VET ADMIN': 'DEPT OF VETERANS AFFAIRS',
    'CORDISH CO': 'CORDISH COMPANIES',
    'ATRIUM': 'ATRIUM HEALTH',                    # bare name does not identify the health system
    'ATRIUM HEALTH CAROLINAS MEDICAL CENTER': 'ATRIUM HEALTH',
}

# No global identity proof, or the proposal loses a distinct entity/facility.
PRESERVED = {
    'NMRK', 'JPMC', 'KW REALTY', 'ARMY', 'ADL', 'FACEBOOK', 'DWT',
    'MILLENNIUM CAPITAL MANAGEMENT', 'MILLENNIUM CAPITAL',
    'VETERANS ADMINISTRATION', 'US VET ADMIN', 'CORDISH CO', 'ATRIUM',
    'ATRIUM HEALTH CAROLINAS MEDICAL CENTER',
}


def _rows():
    with EMPLOYER_NAME_RULES_CSV.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize('variant,canonical', sorted(VERIFIED.items()))
def test_verified_pairs_map_to_one_name(variant, canonical):
    if variant in PRESERVED:
        assert variant not in EMPLOYER_SYNONYMS
        assert normalize_employer_display_name(variant) == variant
        assert normalize_employer_display_name(variant) != canonical
    else:
        assert EMPLOYER_SYNONYMS.get(variant) == canonical
        # previous_employer follows the same supported rule.
        assert normalize_employer_display_name(variant) == canonical


def test_rejected_pairs_stay_separate():
    # campus Hillels are separate organisations; bare CHASE also names
    # Chase Corporation once 'CORP' is stripped; neither is provable
    for name in ('HILLEL', 'CHASE', 'UNITED HEALTHCARE', 'UNITED HEALTH GROUP'):
        assert name not in EMPLOYER_SYNONYMS


def test_softerware_is_a_real_company_not_a_typo():
    # SofterWare Inc (DonorPerfect), Horsham PA: the old rule made it the bare word SOFTWARE
    assert 'SOFTERWARE' not in EMPLOYER_SYNONYMS
    assert 'SOFTWARE' not in set(EMPLOYER_SYNONYMS.values())
    assert normalize_employer_display_name('SOFTERWARE') == 'SOFTERWARE'


def test_new_manual_targets_are_uppercase_and_suffix_free():
    rows = [row for row in _rows() if row['variant'] in VERIFIED]
    targets = {row['canonical'] for row in rows}
    assert targets == set(VERIFIED.values())
    for row in rows:
        assert row['action'] == ('separate' if row['variant'] in PRESERVED else 'rename')
    for target in targets:
        assert target == target.upper()
        assert not LEGAL_SUFFIX_RE.search(target), target


def test_no_rule_points_at_the_joined_spellings():
    # the misspellings must never be a canonical target again
    values = {row['canonical'] for row in _rows()}
    assert not values & {'TWINCITY FAN', 'ATRIUMHEALTH', 'ABINITIO SOFTWARE', 'VETERANS ADMINISTRATION'}


def test_no_duplicate_variants():
    variants = [row['variant'].strip().upper() for row in _rows()]
    assert len(variants) == len(set(variants))


def test_barry_history_converges_on_one_twin_city_fan_row():
    # one donor's 26 filings were split across TWINCITY FAN and TWIN CITY FAN COMPANIES LTD
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 4,
        'contributor_employer': ['TWINCITY FAN', 'TWIN CITY FAN',
                                 'TWIN CITY FAN COMPANIES LTD', 'TWIN CITY FAN COMPANIES LTD'],
        'previous_employer': [''] * 4,
    })
    finalize_employer_names(df)
    assert df['contributor_employer'].nunique() == 1
    assert df['contributor_employer'].iloc[0] == 'TWIN CITY FAN COMPANIES LTD'


def test_apply_synonyms_rewrites_the_audit_examples():
    df = pd.DataFrame({
        'entity_type': ['INDIVIDUAL'] * 6,
        'contributor_employer': ['SPGI', 'NMRK', 'JPMC', 'C21 STORES', 'ABINITIO SOFTWARE', 'SOFTERWARE'],
    })
    df, _ = apply_employer_synonyms(df)
    assert df['contributor_employer'].tolist() == [
        'S&P GLOBAL', 'NMRK', 'JPMC', 'CENTURY 21 STORES',
        'AB INITIO SOFTWARE', 'SOFTERWARE',
    ]
