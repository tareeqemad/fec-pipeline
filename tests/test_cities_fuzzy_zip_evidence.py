"""City names change only through formatting and explicit correction tables."""

import numpy as np
import pandas as pd

from fec.cleaning.addresses.cities import (
    CITY_TABLE_FIXED,
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


class TestCitiesStayAsFiled:
    def test_east_hartford_is_not_renamed_west_hartford(self):
        # the one real row from the data: PO BOX in East Hartford, ZIP 06128
        df = _rows(('EAST HARTFORD', 'CT', '06128', 1),
                   ('WEST HARTFORD', 'CT', '06107', 6), ('WEST HARTFORD', 'CT', '06117', 6))
        cleaned, counts = clean_cities(df)
        assert cleaned['contributor_city'].iloc[0] == 'EAST HARTFORD'
        assert counts == {'known_fixes': 0, 'punctuation_cleaned': 0}

    def test_lookalike_town_without_shared_zip_stays(self):
        # These are different towns despite their similar spelling.
        df = _rows(('LOS ALTOS', 'CA', '94022', 1), ('LOS GATOS', 'CA', '95030', 12))
        cleaned, _ = clean_cities(df)
        assert cleaned['contributor_city'].iloc[0] == 'LOS ALTOS'

    def test_typo_with_shared_zip_is_not_guessed(self):
        df = _rows(('CARLESBAD', 'CA', '920091234', 1), ('CARLSBAD', 'CA', '92009', 12))
        cleaned, counts = clean_cities(df)
        assert cleaned['contributor_city'].iloc[0] == 'CARLESBAD'
        assert counts == {'known_fixes': 0, 'punctuation_cleaned': 0}
        assert bool(cleaned[CITY_TABLE_FIXED].iloc[0]) is False

    def test_matching_zip_does_not_authorize_a_substitution(self):
        df = _rows(('CARLESBAD', 'CA', '92009', 1), ('CARLESBAD', 'CA', '90210', 1),
                   ('CARLSBAD', 'CA', '92009', 12))
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned)[:2] == ['CARLESBAD', 'CARLESBAD']

    def test_frequent_name_in_one_state_does_not_rename_other_records(self):
        df = _rows(('CARLESBAD', 'CA', '92009', 1), ('CARLSBAD', 'CA', '92009', 12),
                   ('CARLESBAD', 'NM', '92009', 1))
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned)[0] == 'CARLESBAD'
        assert _cities(cleaned)[-1] == 'CARLESBAD'

    def test_qualifier_swap_rejected_even_with_shared_zip(self):
        df = _rows(('EAST ORANGE', 'NJ', '07017', 1), ('WEST ORANGE', 'NJ', '07017', 1),
                   ('WEST ORANGE', 'NJ', '07052', 12))
        cleaned, _ = clean_cities(df)
        assert cleaned['contributor_city'].iloc[0] == 'EAST ORANGE'


class TestCounts:
    def test_blank_city_is_not_counted_as_a_change(self):
        df = pd.DataFrame({'contributor_city': [np.nan, 'APT 5', 'LOS ANGELS'],
                           'contributor_state': ['NY', 'NY', 'CA'],
                           'contributor_zip': ['10001', '10001', '90012']})
        cleaned, counts = clean_cities(df)
        assert counts == {'known_fixes': 1, 'punctuation_cleaned': 0}
        assert pd.isna(cleaned['contributor_city'].iloc[0])
        assert pd.isna(cleaned['contributor_city'].iloc[1])


class TestZipScopedKnownFixes:
    def test_ny_follows_the_zip(self):
        df = pd.DataFrame({
            'contributor_city': ['NY', 'NY', 'NY', 'NY', 'NY', 'NY'],
            'contributor_state': ['NY', 'NY', 'NY', 'NY', 'NY', 'NJ'],
            'contributor_zip': ['10075', '11204', '112390000', '10462', '11691', '10075'],
        })
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned) == ['NEW YORK', 'BROOKLYN', 'BROOKLYN', 'BRONX', 'NY', 'NY']

    def test_ny_without_zip_column_stays(self):
        df = pd.DataFrame({'contributor_city': ['NY'], 'contributor_state': ['NY']})
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned) == ['NY']

    def test_scottdale_is_scottsdale_only_in_arizona(self):
        df = pd.DataFrame({'contributor_city': ['SCOTTDALE'] * 3,
                           'contributor_state': ['AZ', 'PA', 'GA'],
                           'contributor_zip': ['85258', '15683', '30079']})
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned) == ['SCOTTSDALE', 'SCOTTDALE', 'SCOTTDALE']

    def test_table_fix_is_flagged_for_the_same_street_recovery(self):
        df = pd.DataFrame({'contributor_city': ['LOS ANGELS', 'MANHATTAN BEACH', 'los angeles.'],
                           'contributor_state': ['CA'] * 3,
                           'contributor_zip': ['90266'] * 3})
        cleaned, _ = clean_cities(df)
        assert _cities(cleaned) == ['LOS ANGELES', 'MANHATTAN BEACH', 'LOS ANGELES']
        # Mark table corrections separately from punctuation and case changes.
        assert cleaned[CITY_TABLE_FIXED].tolist() == [True, False, False]
