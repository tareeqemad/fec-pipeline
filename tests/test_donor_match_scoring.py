"""Donor match scoring (compute_score) including the retired-donor bonus."""

import pandas as pd
import pytest


def _donor_rows(name_a, name_b, street_a, street_b, employer_a="", employer_b=""):
    """Build two individual records for donor-matching safety tests."""
    return pd.DataFrame([
        {
            "entity_type": "INDIVIDUAL", "contributor_name": name_a,
            "contributor_city": "NEW YORK", "contributor_state": "NY",
            "contributor_zip": "10128", "contributor_street_1": street_a,
            "contributor_employer": employer_a, "occupation_category": "RETIRED",
        },
        {
            "entity_type": "INDIVIDUAL", "contributor_name": name_b,
            "contributor_city": "NEW YORK", "contributor_state": "NY",
            "contributor_zip": "10128", "contributor_street_1": street_b,
            "contributor_employer": employer_b, "occupation_category": "RETIRED",
        },
    ])


class TestDonorMatchScoring:
    def test_same_street_scores_high(self):
        from fec.donor_match import compute_score
        p1 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': {'123 MAIN ST'}, 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=5)
        assert score >= 50

    def test_no_corroboration_capped(self):
        from fec.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': '', 'city': '', 'zip5': '',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=20)
        assert score < 50

    def test_middle_name_conflict_blocks(self):
        from fec.donor_match import compute_score
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
        from fec.donor_match import compute_score
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
        from fec.donor_match import compute_score
        p1 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        p2 = {'streets': set(), 'norm_employers': set(),
               'state': 'NY', 'city': 'NEW YORK', 'zip5': '10001',
               'middle': '', 'name': 'SMITH, JOHN'}
        score, signals = compute_score(p1, p2, name_freq=50)
        assert not any('retired_no_emp' in s for s in signals)


class TestSurnameVariantSafety:
    def test_same_zip_alone_does_not_merge_different_people(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "KOPEL, JULIE", "LOBEL, JULIE", "45 E 89TH ST", "1095 PARK AVE"
        )
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 2

    def test_same_street_still_merges_a_surname_typo(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "EPSTEIN, BARBARA", "EPSTIEN, BARBARA",
            "24530 TWICKENHAM DR", "24530 TWICKENHAM DR",
        )
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 1

    def test_same_zip_and_employer_still_merges_a_surname_typo(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "MYERE, LUANN", "MYERS, LUANN", "108 BEACH AVE", "201 BEACH AVE",
            "ACME LLC", "ACME",
        )
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 1


class TestCrossNameSafety:
    def test_first_name_variants_need_an_identity_anchor(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "FIFE, LORI", "FIFE, LORIN", "998 5TH AVE", "5003 BELLAIRE AVE"
        )
        rows.loc[1, ["contributor_city", "contributor_state", "contributor_zip"]] = [
            "VALLEY VILLAGE", "CA", "91607",
        ]
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 2

    def test_first_name_variants_merge_with_the_same_street(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "DOE, STEPHEN", "DOE, STEVEN", "9 STAR FARM RD", "9 STAR FARM RD"
        )
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 1

    def test_curated_different_people_stay_separate_even_with_an_anchor(self):
        from fec.donor_match import match_donors

        rows = _donor_rows(
            "PRINCE, STEPHEN", "PRINCE, STEVEN", "9 STAR FARM RD", "9 STAR FARM RD"
        )
        keys, _ = match_donors(rows)

        assert len(set(keys.values())) == 2


class TestIdentityMergeQualityGate:
    @staticmethod
    def _audit(signals):
        return [{
            "rid_a": "DOE, JOHN|NEW YORK|NY",
            "rid_b": "DOE, JON|NEW YORK|NY",
            "merged": True,
            "signals": signals,
        }]

    @staticmethod
    def _keys(key_a="same", key_b="same"):
        return {
            "DOE, JOHN|NEW YORK|NY": key_a,
            "DOE, JON|NEW YORK|NY": key_b,
        }

    def test_accepts_cross_name_with_zip_anchor(self):
        from fec.donor_match.matcher import _validate_merge_audit

        result = _validate_merge_audit(
            self._keys(), self._audit("zip5=10001(+25); cross_name(+10)")
        )

        assert result["checked"] == 1
        assert result["cross_name_zip_only"] == 1

    def test_rejects_cross_name_without_identity_anchor(self):
        from fec.donor_match.matcher import _validate_merge_audit

        with pytest.raises(ValueError, match="quality gate rejected"):
            _validate_merge_audit(
                self._keys(), self._audit("state=NY(+10); cross_name(+10)")
            )

    def test_ignores_merge_ejected_by_chain_validation(self):
        from fec.donor_match.matcher import _validate_merge_audit

        result = _validate_merge_audit(
            self._keys("one", "two"), self._audit("CROSS_NAME_NO_ANCHOR")
        )

        assert result["checked"] == 0


class TestChainValidation:
    def test_record_count_tie_has_a_stable_canonical_rid(self, monkeypatch):
        from fec.donor_match import matcher

        rids = ["DOE, JOHN|A|NY", "DOE, JOHN|B|NY",
                "DOE, JOHN|C|NY", "DOE, JOHN|D|NY"]
        union = matcher.UnionFind()
        for rid in rids:
            union.union(rids[0], rid)
        profiles = {
            rid: {"rid": rid, "record_count": 1, "norm_name": "DOE|JOHN"}
            for rid in rids
        }
        canonicals = []

        def score(canonical, _other, _frequency):
            canonicals.append(canonical["rid"])
            return 100, []

        monkeypatch.setattr(matcher, "compute_score", score)
        matcher._build_and_validate_chains(
            union, profiles, {"DOE|JOHN": rids}
        )

        assert set(canonicals) == {"DOE, JOHN|D|NY"}
