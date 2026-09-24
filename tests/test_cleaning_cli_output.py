"""clean.py checks its output before saving it, and keeps foreign postcodes."""
import pandas as pd
import pytest

from fec.cleaning import cli


def test_foreign_postcode_is_kept_and_us_zip_is_five_digits():
    df = pd.DataFrame({
        'contributor_city': ['LONDON', 'CHICAGO'],
        'contributor_state': ['', 'IL'],
        'contributor_street_1': ['10 DOWNING ST', '1 MAIN ST'],
        'contributor_street_2': ['', ''],
        'contributor_zip': ['SW1A 2AA', '606011234'],
    })

    cli._ensure_zip_format(df)

    assert df['contributor_zip'].tolist() == ['SW1A 2AA', '60601']


def test_failed_quality_gates_keep_the_previous_cleaned_file(tmp_path, monkeypatch):
    output = tmp_path / 'contributions_cleaned.csv'
    output.write_text('previous good file\n', encoding='utf-8')
    cleaned = pd.DataFrame({
        'sub_id': ['1'], 'recipient_committee': [None], 'contributor_zip': ['60601'],
        'contributor_city': ['CHICAGO'], 'contributor_state': ['IL'],
        'contributor_street_1': ['1 MAIN ST'], 'contributor_street_2': [''],
    })
    monkeypatch.setattr(cli, 'CLEANED_CSV', output)
    monkeypatch.setattr(cli, 'read_pipeline_csv', lambda path: cleaned.copy())
    monkeypatch.setattr(cli, 'clean_pipeline', lambda df, out_dir: (df, pd.DataFrame(), None))
    monkeypatch.setattr(cli, 'run_quality_gates', lambda df: {
        'passed': False, 'checks': {}, 'issues': ['1 rows with a missing amount']})

    with pytest.raises(SystemExit):
        cli.main()

    assert output.read_text(encoding='utf-8') == 'previous good file\n'
    assert (tmp_path / 'quality_gates.json').exists()
