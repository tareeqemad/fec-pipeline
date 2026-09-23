"""Foreign addresses are detected on the raw filing, kept exactly as filed, and never geocoded."""
import numpy as np
import pandas as pd

from fec.cleaning.foreign_addresses import (
    foreign_address_mask,
    restore_foreign_addresses,
    snapshot_foreign_addresses,
)


def _frame(rows):
    return pd.DataFrame(rows, columns=[
        'sub_id', 'contributor_street_1', 'contributor_street_2',
        'contributor_city', 'contributor_state', 'contributor_zip',
    ])


def test_mask_detects_foreign_and_spares_us_streets_named_after_places():
    df = _frame([
        ('1', '13 HISH ST.', '', 'REHOVOT', 'CA', '95066'),               # foreign city list
        ('2', 'HASHOFET CHAIM COHEN 16/24', '', 'JERUSALEM, ISRAEL', 'NY', '93500'),  # country in city
        ('3', 'EHUD MANOR 5', '', 'NETANYA', 'WA', '98040'),
        ('4', '10 DOWNING ST', '', 'LONDON', 'NY', '10001'),               # ambiguous city, wrong state
        ('5', '100 MAIN ST', '', 'LONDON', 'KY', '40741'),                 # London, Kentucky
        ('6', '7511 LONDON LN', '', 'BOCA RATON', 'FL', '33433'),          # US street named London
        ('7', '6131 GULF OF MEXICO DR', '', 'LONGBOAT KEY', 'FL', '34228'),
        ('8', '112 INDIA ST', '', 'BROOKLYN', 'NY', '11222'),
        ('9', 'PO BOX 12', 'HAIFA ISRAEL', 'HAIFA', 'NJ', '07000'),        # country at end of street_2
    ])
    mask = foreign_address_mask(df)
    assert mask.tolist() == [True, True, True, True, False, False, False, False, True]


def test_snapshot_and_restore_put_the_filed_address_back_and_drop_coordinates():
    raw = _frame([
        ('1', 'EHUD MANOR 5', '', 'NETANYA', 'WA', '98040'),
        ('2', '1 MAIN ST', '', 'SEATTLE', 'WA', '98101'),
    ])
    snapshot = snapshot_foreign_addresses(raw)
    assert list(snapshot.index) == ['1']

    cleaned = raw.copy()
    # what the US-only repairs would do to the foreign row
    cleaned.loc[0, ['contributor_street_1', 'contributor_city', 'contributor_zip']] = ['1191 2ND AVE', 'SEATTLE', '98101']
    cleaned['latitude'] = [47.6, 47.6]
    cleaned['longitude'] = [-122.3, -122.3]
    cleaned['geocode_level'] = ['rooftop', 'rooftop']

    n = restore_foreign_addresses(cleaned, snapshot)
    assert n == 3
    assert cleaned.loc[0, ['contributor_street_1', 'contributor_city', 'contributor_zip']].tolist() == ['EHUD MANOR 5', 'NETANYA', '98040']
    assert np.isnan(cleaned.loc[0, 'latitude']) and np.isnan(cleaned.loc[0, 'longitude'])
    # the US row is untouched
    assert cleaned.loc[1, 'contributor_city'] == 'SEATTLE' and cleaned.loc[1, 'latitude'] == 47.6


def test_restore_is_a_no_op_without_foreign_rows():
    raw = _frame([('1', '1 MAIN ST', '', 'SEATTLE', 'WA', '98101')])
    snapshot = snapshot_foreign_addresses(raw)
    assert snapshot.empty
    assert restore_foreign_addresses(raw.copy(), snapshot) == 0
