"""Fuzzy and known city fixes need ZIP evidence: a lookalike real town is never renamed into its neighbour."""

import json

import numpy as np
import pandas as pd

from fec.cleaning.addresses.cities import (
    CITY_TABLE_FIXED,
    _auto_detect_city_typos,
    _swaps_place_qualifier,
    clean_cities,
)


def _rows(*groups):
    """groups of (city, state, zip, count) -> a frame with that many identical rows each."""
    records = []
    for city, state, zip_code, count in groups:
        records += [{'contributor_city': city, 'contributor_state': state, 'contributor_zip': zip_code}] * count
    return pd.DataFrame(records)


def _cities(df):
    return df['contributor_city'].tolist()


class TestFuzzyNeedsZipEvidence:
    def test_east_hartford_is_not_renamed_west_hartford(self):
        # the one real row from the data: PO BOX in East Hartford, ZIP 06128
        df = _rows(('EAST HARTFORD', 'CT', '06128', 1),
                   ('WEST HARTFORD', 'CT', '06107', 6), ('WEST HARTFORD', 'CT', '06117', 6))
        cleaned, counts = clean_cities(df, fuzzy=True)
        assert cleaned['contributor_city'].iloc[0] == 'EAST HARTFORD'
        assert counts['fuzzy_fixes'] == 0

    def test_lookalike_town_without_shared_zip_stays(self):
        # LOS ALTOS / LOS GATOS: ratio 0.889, no qualifier word, only the ZIP tells them apart
        df = _rows(('LOS ALTOS', 'CA', '94022', 1), ('LOS GATOS', 'CA', '95030', 12))
        assert _auto_detect_city_typos(df) == {}
        cleaned, _ = clean_cities(df, fuzzy=True)
        assert cleaned['contributor_city'].iloc[0] == 'LOS ALTOS'

    def test_typo_with_shared_zip_is_fixed(self):
        df = _rows(('CARLESBAD', 'CA', '920091234', 1), ('CARLSBAD', 'CA', '92009', 12))
        cleaned, counts = clean_cities(df, fuzzy=True)
        assert cleaned['contributor_city'].iloc[0] == 'CARLSBAD'
        assert counts['fuzzy_fixes'] == 1
        assert bool(cleaned[CITY_TABLE_FIXED].iloc[0]) is True

    def test_fix_applies_only_at_the_evidenced_zip(self):
        # same spelling at a ZIP nobody files with CARLSBAD: left as filed
        df = _rows(('CARLESBAD', 'CA', '92009', 1), ('CARLESBAD', 'CA', '90210', 1),
                   ('CARLSBAD', 'CA', '92009', 12))
        cleaned, _ = clean_cities(df, fuzzy=True)
        assert _cities(cleaned)[:2] == ['CARLSBAD', 'CARLESBAD']

    def test_fix_learned_in_one_state_does_not_rename_another_state(self):
        df = _rows(('CARLESBAD', 'CA', '92009', 1), ('CARLSBAD', 'CA', '92009', 12),
                   ('CARLESBAD', 'NM', '92009', 1))
        cleaned, _ = clean_cities(df, fuzzy=True)
        assert _cities(cleaned)[0] == 'CARLSBAD'
        assert _cities(cleaned)[-1] == 'CARLESBAD'

    def test_donor_own_other_filings_count_as_evidence(self):
        # LOXAHATCHEE 33470 is filed by one donor only; that is still evidence
        df = _rows(('LOXAHACHEE', 'FL', '33470', 2), ('LOXAHATCHEE', 'FL', '33470', 12))
        assert _auto_detect_city_typos(df) == {('FL', 'LOXAHACHEE', '33470'): 'LOXAHATCHEE'}

    def test_qualifier_swap_rejected_even_with_shared_zip(self):
        df = _rows(('EAST ORANGE', 'NJ', '07017', 1), ('WEST ORANGE', 'NJ', '07017', 1),
                   ('WEST ORANGE', 'NJ', '07052', 12))
        cleaned, _ = clean_cities(df, fuzzy=True)
        assert cleaned['contributor_city'].iloc[0] == 'EAST ORANGE'

    def test_no_zip_column_means_no_fuzzy_fix(self):
        df = pd.DataFrame({'contributor_city': ['CARLESBAD'] + ['CARLSBAD'] * 12,
                           'contributor_state': ['CA'] * 13})
        assert _auto_detect_city_typos(df) == {}

    def test_report_lists_state_city_zip(self, tmp_path):
        df = _rows(('CARLESBAD', 'CA', '92009', 1), ('CARLSBAD', 'CA', '92009', 12),
                   ('EAST HARTFORD', 'CT', '06128', 1), ('WEST HARTFORD', 'CT', '06107', 12))
        clean_cities(df, fuzzy=True, report_dir=str(tmp_path))
        report = json.loads((tmp_path / 'auto_city_fixes.json').read_text(encoding='utf-8'))
        assert report == [{'state': 'CA', 'city': 'CARLESBAD', 'zip5': '92009', 'fix': 'CARLSBAD'}]


class TestPlaceQualifier:
    def test_swapped_or_dropped_qualifier(self):
        assert _swaps_place_qualifier('EAST HARTFORD', 'WEST HARTFORD')
        assert _swaps_place_qualifier('WEST BLOOMFIELD TOWNSHIP', 'BLOOMFIELD TOWNSHIP')
        assert _swaps_place_qualifier('UPPER BROOKVILLE', 'LOWER BROOKVILLE')

    def test_typos_are_not_qualifier_swaps(self):
        assert not _swaps_place_qualifier('FT WASHINGTON', 'FORT WASHINGTON')
        assert not _swaps_place_qualifier('SNOW MASS VILLAGE', 'SNOWMASS VILLAGE')
        assert not _swaps_place_qualifier('PLEASANVILLE', 'PLEASANTVILLE')


class TestCounts:
    def test_blank_city_is_not_counted_as_a_change(self):
        df = pd.DataFrame({'contributor_city': [np.nan, 'APT 5', 'LOS ANGELS'],
                           'contributor_state': ['NY', 'NY', 'CA'],
                           'contributor_zip': ['10001', '10001', '90012']})
        cleaned, counts = clean_cities(df, fuzzy=True)
        assert counts == {'known_fixes': 1, 'fuzzy_fixes': 0, 'punctuation_cleaned': 0}
        assert pd.isna(cleaned['contributor_city'].iloc[0])
        assert pd.isna(cleaned['contributor_city'].iloc[1])


class TestZipScopedKnownFixes:
    def test_ny_follows_the_zip(self):
        df = pd.DataFrame({
            'contributor_city': ['NY', 'NY', 'NY', 'NY', 'NY', 'NY'],
            'contributor_state': ['NY', 'NY', 'NY', 'NY', 'NY', 'NJ'],
            'contributor_zip': ['10075', '11204', '112390000', '10462', '11691', '10075'],
        })
        cleaned, _ = clean_cities(df, fuzzy=False)
        assert _cities(cleaned) == ['NEW YORK', 'BROOKLYN', 'BROOKLYN', 'BRONX', 'NY', 'NY']

    def test_ny_without_zip_column_stays(self):
        df = pd.DataFrame({'contributor_city': ['NY'], 'contributor_state': ['NY']})
        cleaned, _ = clean_cities(df, fuzzy=False)
        assert _cities(cleaned) == ['NY']

    def test_scottdale_is_scottsdale_only_in_arizona(self):
        df = pd.DataFrame({'contributor_city': ['SCOTTDALE'] * 3,
                           'contributor_state': ['AZ', 'PA', 'GA'],
                           'contributor_zip': ['85258', '15683', '30079']})
        cleaned, _ = clean_cities(df, fuzzy=False)
        assert _cities(cleaned) == ['SCOTTSDALE', 'SCOTTDALE', 'SCOTTDALE']

    def test_table_fix_is_flagged_for_the_same_street_recovery(self):
        df = pd.DataFrame({'contributor_city': ['LOS ANGELS', 'MANHATTAN BEACH', 'los angeles.'],
                           'contributor_state': ['CA'] * 3,
                           'contributor_zip': ['90266'] * 3})
        cleaned, _ = clean_cities(df, fuzzy=False)
        assert _cities(cleaned) == ['LOS ANGELES', 'MANHATTAN BEACH', 'LOS ANGELES']
        # punctuation/case clean-up is not a guess; the typo-table rewrite is
        assert cleaned[CITY_TABLE_FIXED].tolist() == [True, False, False]
