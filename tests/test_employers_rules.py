"""employer_name_rules.csv: web-verified same-company pairs from the 2026-09-23 audit (findings 17/33)."""
import csv

import pandas as pd
import pytest

from fec.cleaning.employer_synonyms import (
    EMPLOYER_SYNONYMS,
    apply_employer_synonyms,
    finalize_employer_names,
    normalize_employer_display_name,
)
from fec.config.constants import LEGAL_SUFFIX_RE
from fec.env import EMPLOYER_NAME_RULES_CSV

# variant -> canonical, each pair checked against the company's own sources
VERIFIED = {
    'AB INITIO': 'AB INITIO SOFTWARE',            # Ab Initio Software LLC, 201 Spring St, Lexington MA
    'ABINITIO': 'AB INITIO SOFTWARE',
    'ABINITIO SOFTWARE': 'AB INITIO SOFTWARE',
    'SPGI': 'S&P GLOBAL',                         # NYSE ticker of S&P Global Inc.
    'NMRK': 'NEWMARK GROUP',                      # Nasdaq ticker of Newmark Group Inc.
    'JPMC': 'JPMORGAN CHASE',                     # JPMorgan Chase's own abbreviation
    'C21 STORES': 'CENTURY 21 STORES',            # the Gindi family's store, c21stores.com
    'C21STORES': 'CENTURY 21 STORES',
    'KW REALTY': 'KELLER WILLIAMS',               # Keller Williams agents in Goshen NY
    'TWINCITY FAN': 'TWIN CITY FAN COMPANIES',    # Twin City Fan Companies Ltd, Minneapolis
    'TWIN CITY FAN': 'TWIN CITY FAN COMPANIES',
    'MILE ONE': 'MILEONE AUTOGROUP',              # MileOne Autogroup, Towson MD
    'WARNER BROTHERS': 'WARNER BROS',
    'ARMY': 'US ARMY',
    'DEPT OF ARMY': 'US ARMY',
    'ADL': 'ANTI-DEFAMATION LEAGUE',
    'FACEBOOK': 'META',                           # Facebook Inc renamed Meta Platforms 2021
    'DWT': 'DAVIS WRIGHT TREMAINE',               # dwt.com
    'U.C. REGENTS': 'UNIVERSITY OF CALIFORNIA',   # The Regents = UC's corporate name
    'UC REGENTS': 'UNIVERSITY OF CALIFORNIA',
    'MILLENNIUM CAPITAL MANAGEMENT': 'MILLENNIUM MANAGEMENT',
    'MILLENNIUM CAPITAL': 'MILLENNIUM MANAGEMENT',
    'VETERANS ADMINISTRATION': 'DEPT OF VETERANS AFFAIRS',  # renamed 1989
    'US VET ADMIN': 'DEPT OF VETERANS AFFAIRS',
    'CORDISH CO': 'CORDISH COMPANIES',
    'ATRIUM': 'ATRIUM HEALTH',                    # the system's name is two words
    'ATRIUM HEALTH CAROLINAS MEDICAL CENTER': 'ATRIUM HEALTH',
}


def _rows():
    with EMPLOYER_NAME_RULES_CSV.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize('variant,canonical', sorted(VERIFIED.items()))
def test_verified_pairs_map_to_one_name(variant, canonical):
    assert EMPLOYER_SYNONYMS.get(variant) == canonical
    # previous_employer path lands on the same name
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
    targets = {row['canonical'] for row in _rows() if row['variant'] in VERIFIED}
    assert targets == set(VERIFIED.values())
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
        'S&P GLOBAL', 'NEWMARK GROUP', 'JPMORGAN CHASE', 'CENTURY 21 STORES',
        'AB INITIO SOFTWARE', 'SOFTERWARE',
    ]
