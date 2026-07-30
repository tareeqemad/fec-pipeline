"""City-name normalization map and trailing street-direction handling."""


class TestCityNormalize:
    def test_s_orange_to_south_orange(self):
        from fec.config import CITY_NORMALIZE
        assert CITY_NORMALIZE['S ORANGE'] == 'SOUTH ORANGE'

    def test_so_orange_to_south_orange(self):
        from fec.config import CITY_NORMALIZE
        assert CITY_NORMALIZE['SO ORANGE'] == 'SOUTH ORANGE'


class TestTrailingDirection:
    def test_trailing_s_moved_to_prefix(self):
        import re
        s = '101 WESTON LN S'
        s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert s == '101 S WESTON LN'

    def test_trailing_nw_moved_to_prefix(self):
        import re
        s = '500 MAIN AVE NW'
        s = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert s == '500 NW MAIN AVE'

    def test_no_trailing_direction_unchanged(self):
        import re
        s = '123 MAIN ST'
        result = re.sub(r'^(\d+)\s+(.+?)\s+(N|S|E|W|NE|NW|SE|SW)\s*$', r'\1 \3 \2', s)
        assert result == '123 MAIN ST'
