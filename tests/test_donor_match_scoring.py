"""Donor match scoring (compute_score) including the retired-donor bonus."""


class TestDonorMatchScoring:
    def test_same_street_scores_high(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=5)
        assert score >= 50

    def test_no_corroboration_capped(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=20)
        assert score < 50

    def test_middle_name_conflict_blocks(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': 'DAVID', 'name': 'SMITH, JOHN'}
        p2 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': 'MICHAEL', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=5)
        assert score < 0  # HARD_BLOCK


class TestRetiredDonorBonus:
    def test_retired_rare_name_with_geo_gets_bonus(self):
        from fec.database.donor_match import compute_score
        # same state = geo overlap; no employers = retired-like
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': 'CA', 'city': 'LOS ANGELES', 'zip5': '90210',
               'middle': '', 'name': 'KOUM, JAN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': 'CA', 'city': 'SAN FRANCISCO', 'zip5': '94102',
               'middle': '', 'name': 'KOUM, JAN'}
        score, signals = compute_score(p1, p2, name_freq=2)
        assert any('retired_no_emp' in s for s in signals)

    def test_common_name_no_employer_no_bonus(self):
        from fec.database.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=50)
        assert not any('retired_no_emp' in s for s in signals)
