"""Same-street alignment never writes a city the row's ZIP contradicts, and never merges two real addresses."""

import pandas as pd

from fec.cleaning.addresses import CITY_TABLE_FIXED
from fec.cleaning.pipeline.address_fixes.same_street import _recover_address_from_same_street


def _frame(*groups):
    """groups of (name, street, city, state, zip, count[, table_fixed]) -> one row per filing."""
    records = []
    for group in groups:
        name, street, city, state, zip_code, count = group[:6]
        fixed = group[6] if len(group) > 6 else False
        records += [{
            'contributor_name': name, 'entity_type': 'INDIVIDUAL',
            'contributor_street_1': street, 'contributor_city': city,
            'contributor_state': state, 'contributor_zip': zip_code,
            CITY_TABLE_FIXED: fixed,
        }] * count
    return pd.DataFrame(records).reset_index(drop=True)


def _places(df, name, street=None):
    rows = df[df['contributor_name'] == name]
    if street is not None:
        rows = rows[rows['contributor_street_1'] == street]
    return sorted(set(zip(rows['contributor_city'], rows['contributor_zip'])))


# other people who file MANHATTAN BEACH with 90266 (the ZIP belongs only to it)
_MANHATTAN_BEACH = ('OTHER, PERSON', '1 OCEAN DR', 'MANHATTAN BEACH', 'CA', '90266', 5)


class TestCityGuard:
    def test_newman_keeps_manhattan_beach_and_the_typo_rows_join_it(self):
        # raw 'LOS ANGELS' (4 rows, turned into LOS ANGELES by the typo table) beat
        # 3 correct MANHATTAN BEACH rows 4-3 in the old vote
        df = _frame(
            ('NEWMAN, SAM', '118 S POINSETTIA AVE', 'LOS ANGELES', 'CA', '90266', 4, True),
            ('NEWMAN, SAM', '118 S POINSETTIA AVE', 'MANHATTAN BEACH', 'CA', '90266', 3),
            ('NEWMAN, SAMUEL', '118 S POINSETTIA AVE', 'MANHATTAN BEACH', 'CA', '90266', 8),
            _MANHATTAN_BEACH,
            ('ANGELENO, ANN', '5 MAIN ST', 'LOS ANGELES', 'CA', '90012', 5),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'NEWMAN, SAM') == [('MANHATTAN BEACH', '90266')]

    def test_a_city_the_filer_chose_is_not_replaced_without_being_a_guess(self):
        # same shape, but LOS ANGELES was filed, not produced by a typo table:
        # the MANHATTAN BEACH rows still keep their city, the filed rows stay too
        df = _frame(
            ('NEWMAN, SAM', '118 S POINSETTIA AVE', 'LOS ANGELES', 'CA', '90266', 4),
            ('NEWMAN, SAM', '118 S POINSETTIA AVE', 'MANHATTAN BEACH', 'CA', '90266', 3),
            _MANHATTAN_BEACH,
            ('ANGELENO, ANN', '5 MAIN ST', 'LOS ANGELES', 'CA', '90012', 5),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'NEWMAN, SAM') == [('LOS ANGELES', '90266'), ('MANHATTAN BEACH', '90266')]

    def test_alias_the_zip_carries_is_still_aligned(self):
        # the dominant city IS filed with the row's ZIP by other people: align as before
        df = _frame(
            ('LEE, AMY', '10 ELM ST', 'NEW YORK', 'NY', '10021', 5),
            ('LEE, AMY', '10 ELM ST', 'MANHATTAN', 'NY', '10021', 1),
            ('OTHER, PERSON', '1 PARK AVE', 'NEW YORK', 'NY', '10021', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'LEE, AMY') == [('NEW YORK', '10021')]


class TestSecondAddressSharingStreetText:
    def test_frankel_madison_avenue_keeps_new_york(self):
        # 620 MADISON AVE exists in Manhattan 10022 and in West Hempstead 11552
        df = _frame(
            ('FRANKEL, DOVID', '620 MADISON AVE', 'NEW YORK', 'NY', '10022', 7),
            ('FRANKEL, DOVID', '620 MADISON AVE', 'WEST HEMPSTEAD', 'NY', '11552', 9),
            ('OFFICE, WORKER', '500 MADISON AVE', 'NEW YORK', 'NY', '10022', 4),
            ('NEIGHBOR, NED', '9 HEMPSTEAD AVE', 'WEST HEMPSTEAD', 'NY', '11552', 2),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'FRANKEL, DOVID') == [('NEW YORK', '10022'), ('WEST HEMPSTEAD', '11552')]

    def test_mixed_up_filing_is_still_aligned(self):
        # home street + office city/ZIP: nobody files HICKS ST at 10022, so the row is a mix-up
        df = _frame(
            ('STEINBERG, ADAM', '161 HICKS ST', 'BROOKLYN', 'NY', '11201', 3),
            ('STEINBERG, ADAM', '161 HICKS ST', 'NEW YORK', 'NY', '10022', 1),
            ('OFFICE, WORKER', '500 MADISON AVE', 'NEW YORK', 'NY', '10022', 4),
            ('NEIGHBOR, NED', '20 HICKS ST', 'BROOKLYN', 'NY', '11201', 2),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'STEINBERG, ADAM') == [('BROOKLYN', '11201')]

    def test_zip_typo_on_the_same_city_is_still_fixed(self):
        df = _frame(
            ('BRITVAN, J ALLEN', '8 SPENCER HILL CT', 'PLEASANTVILLE', 'NY', '10570', 5),
            ('BRITVAN, J ALLEN', '8 SPENCER HILL CT', 'PLEASANTVILLE', 'NY', '10590', 2),
            ('OTHER, PERSON', '3 BEDFORD RD', 'PLEASANTVILLE', 'NY', '10570', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'BRITVAN, J ALLEN') == [('PLEASANTVILLE', '10570')]


class TestCutOffCity:
    def test_santa_becomes_santa_fe_on_a_tie(self):
        df = _frame(
            ('SUSSMAN, AVIVA', '1007 PASEO DE LA CUMA', 'SANTA', 'NM', '87501', 2),
            ('SUSSMAN, AVIVA', '1007 PASEO DE LA CUMA', 'SANTA FE', 'NM', '87501', 2),
            ('OTHER, PERSON', '1 CANYON RD', 'SANTA FE', 'NM', '87501', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'SUSSMAN, AVIVA') == [('SANTA FE', '87501')]

    def test_real_village_name_is_not_a_cut_off(self):
        # ELBERON is a place in its own right, not a prefix of LONG BRANCH: kept on a tie
        df = _frame(
            ('HEDAYA, EDWARD', '17 SYCAMORE AVE', 'ELBERON', 'NJ', '07740', 1),
            ('HEDAYA, EDWARD', '17 SYCAMORE AVE', 'LONG BRANCH', 'NJ', '07740', 1),
            ('OTHER, PERSON', '1 OCEAN AVE', 'LONG BRANCH', 'NJ', '07740', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'HEDAYA, EDWARD') == [('ELBERON', '07740'), ('LONG BRANCH', '07740')]


class TestSameZipOtherStreet:
    def test_other_home_in_the_same_zip_does_not_rename_this_one(self):
        # FRANK, BLAIR: PARK CITY at one street, SNYDERVILLE (his own spelling only) at another
        df = _frame(
            ('FRANK, BLAIR', '6300 SAGEWOOD DR', 'PARK CITY', 'UT', '84098', 2),
            ('FRANK, BLAIR', 'OLD RANCH RD', 'SNYDERVILLE', 'UT', '84098', 3),
            ('OTHER, PERSON', '1 MAIN ST', 'PARK CITY', 'UT', '84098', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'FRANK, BLAIR', '6300 SAGEWOOD DR') == [('PARK CITY', '84098')]
        assert _places(df, 'FRANK, BLAIR', 'OLD RANCH RD') == [('SNYDERVILLE', '84098')]

    def test_truncated_city_on_a_unit_row_is_still_recovered(self):
        df = _frame(
            ('LEE, AMY', '10 E 70TH ST', 'NEW YORK', 'NY', '10021', 3),
            ('LEE, AMY', '10 E 70TH ST APT 5', 'NEW', 'NY', '10021', 1),
            ('OTHER, PERSON', '1 PARK AVE', 'NEW YORK', 'NY', '10021', 3),
        )
        _recover_address_from_same_street(df)
        assert _places(df, 'LEE, AMY') == [('NEW YORK', '10021')]
