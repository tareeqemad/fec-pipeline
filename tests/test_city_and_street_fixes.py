"""City-name and street-direction normalization."""

import pandas as pd


class TestCityNormalize:
    def test_s_orange_to_south_orange(self):
        from fec.config.cities import CITY_NORMALIZE
        assert CITY_NORMALIZE['S ORANGE'] == 'SOUTH ORANGE'

    def test_so_orange_to_south_orange(self):
        from fec.config.cities import CITY_NORMALIZE
        assert CITY_NORMALIZE['SO ORANGE'] == 'SOUTH ORANGE'

    def test_ambiguous_city_names_respect_state(self):
        from fec.cleaning.cities import clean_cities

        rows = pd.DataFrame({
            'contributor_city': [
                'EASTHAMPTON', 'EASTHAMPTON',
                'FAIRLAWN', 'FAIRLAWN',
                'DELMAR', 'DELMAR',
            ],
            'contributor_state': ['MA', 'NY', 'OH', 'NJ', 'NY', 'CA'],
        })

        cleaned, _ = clean_cities(rows, fuzzy=False)

        assert cleaned['contributor_city'].tolist() == [
            'EASTHAMPTON', 'EAST HAMPTON',
            'FAIRLAWN', 'FAIR LAWN',
            'DELMAR', 'DEL MAR',
        ]


class TestStreetDirection:
    def test_trailing_s_stays_suffix(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('101 WESTON LN S') == '101 WESTON LN S'

    def test_trailing_nw_stays_suffix(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('500 MAIN AVE NW') == '500 MAIN AVE NW'

    def test_aipac_address_keeps_postdirectional(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('251 H STREET, NW') == '251 H ST NW'

    def test_prefix_direction_stays_prefix(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('500 NORTH MAIN AVENUE') == '500 N MAIN AVE'

    def test_alphanumeric_house_number_stays_joined(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('704C 13TH ST E') == '704C 13TH ST E'

    def test_direction_stuck_to_house_number_is_split(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('9W WALTON ST') == '9 W WALTON ST'

    def test_street_name_stuck_to_house_number_is_split(self):
        from fec.cleaning.street_text import _normalize_street
        assert _normalize_street('13764RIVOLI DRIVE') == '13764 RIVOLI DR'
