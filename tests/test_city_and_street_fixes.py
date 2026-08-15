"""City-name normalization map and trailing street-direction handling."""


class TestCityNormalize:
    def test_s_orange_to_south_orange(self):
        from fec.config.cities import CITY_NORMALIZE
        assert CITY_NORMALIZE['S ORANGE'] == 'SOUTH ORANGE'

    def test_so_orange_to_south_orange(self):
        from fec.config.cities import CITY_NORMALIZE
        assert CITY_NORMALIZE['SO ORANGE'] == 'SOUTH ORANGE'


class TestTrailingDirection:
    def test_trailing_s_moved_to_prefix(self):
        from fec.cleaning.addresses import _normalize_street
        assert _normalize_street('101 WESTON LN S') == '101 S WESTON LN'

    def test_trailing_nw_moved_to_prefix(self):
        from fec.cleaning.addresses import _normalize_street
        assert _normalize_street('500 MAIN AVE NW') == '500 NW MAIN AVE'

    def test_no_trailing_direction_unchanged(self):
        from fec.cleaning.addresses import _normalize_street
        assert _normalize_street('123 MAIN ST') == '123 MAIN ST'
