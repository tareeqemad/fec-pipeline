"""The incremental merge dedups sub_ids left by a crash between the output write and the tracker update."""
import pandas as pd

from fec.cleaning import cli


def test_no_overlap_appends_all():
    existing = pd.DataFrame({'sub_id': ['1', '2'], 'v': ['a', 'b']})
    new = pd.DataFrame({'sub_id': ['3'], 'v': ['c']})
    out = cli._merge_incremental(existing, new)
    assert out['sub_id'].tolist() == ['1', '2', '3']
    assert out['v'].tolist() == ['a', 'b', 'c']


def test_crash_window_duplicates_keep_freshest():
    # Prior run wrote 2,3 but crashed before the tracker update; the re-cleaned (last) version wins.
    existing = pd.DataFrame({'sub_id': ['1', '2', '3'], 'v': ['a', 'old', 'old']})
    new = pd.DataFrame({'sub_id': ['2', '3'], 'v': ['new', 'new']})
    out = cli._merge_incremental(existing, new)
    assert not out['sub_id'].duplicated().any()
    assert out.set_index('sub_id')['v'].to_dict() == {'1': 'a', '2': 'new', '3': 'new'}


def test_identical_rerun_is_idempotent():
    existing = pd.DataFrame({'sub_id': ['1', '2'], 'v': ['a', 'b']})
    out = cli._merge_incremental(existing, existing.copy())
    assert len(out) == 2
    assert not out['sub_id'].duplicated().any()


def test_tracker_treats_numeric_and_text_ids_as_the_same(tmp_path):
    tracker = tmp_path / 'cleaned_ids.csv'
    pd.DataFrame({'sub_id': ['1', '2']}).to_csv(tracker, index=False)

    cli._update_tracker(pd.DataFrame({'sub_id': [1, 2, 3]}), tracker)

    saved = pd.read_csv(tracker, dtype={'sub_id': 'string'})
    assert saved['sub_id'].tolist() == ['1', '2', '3']


def test_date_normalization_mixed_schema_no_caller_mutation():
    existing = pd.DataFrame({'sub_id': ['1'],
                             'contribution_receipt_date': ['2024-01-02'],
                             'donor_key': ['k1']})
    new = pd.DataFrame({'sub_id': ['2', '3'],
                        'contribution_receipt_date': pd.to_datetime(['2024-03-05', None]),
                        'occupation_status': ['DISCLOSED', 'MISSING']})
    out = cli._merge_incremental(existing, new)
    # datetime64 batch dates become plain 'YYYY-MM-DD' strings; NaT -> NaN, not 'NaT'
    assert out['contribution_receipt_date'].tolist()[:2] == ['2024-01-02', '2024-03-05']
    assert pd.isna(out['contribution_receipt_date'].iloc[2])
    # public-schema existing + internal-col batch -> column union, NaN-filled
    assert set(out.columns) == {'sub_id', 'contribution_receipt_date', 'donor_key', 'occupation_status'}
    assert pd.isna(out['occupation_status'].iloc[0])
    # the caller's batch frame must not be mutated
    assert pd.api.types.is_datetime64_any_dtype(new['contribution_receipt_date'])
