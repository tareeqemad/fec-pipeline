"""Real donors have the literal surname 'NULL'; every CSV read must go through read_pipeline_csv."""
import tempfile
from pathlib import Path

import pandas as pd

from fec.io import read_pipeline_csv


def _make_sample() -> pd.DataFrame:
    """3 rows: literal 'NULL' surname, genuinely missing surname, normal."""
    return pd.DataFrame([
        # surname is the literal string "NULL"
        {'sub_id': '1', 'contributor_name': 'NULL, JAMES',
         'contributor_first_name': 'JAMES', 'contributor_last_name': 'NULL'},
        # Genuinely missing surname
        {'sub_id': '2', 'contributor_name': 'ANON',
         'contributor_first_name': None, 'contributor_last_name': None},
        # Normal donor
        {'sub_id': '3', 'contributor_name': 'SMITH, JOHN',
         'contributor_first_name': 'JOHN', 'contributor_last_name': 'SMITH'},
    ])


def _roundtrip(df: pd.DataFrame, reader) -> pd.DataFrame:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / 'sample.csv'
        df.to_csv(path, index=False)
        return reader(path)


def test_read_pipeline_csv_preserves_literal_null():
    """read_pipeline_csv must keep 'NULL' as a string, not NaN."""
    df = _make_sample()
    rt = _roundtrip(df, read_pipeline_csv)

    null_row = rt[rt['sub_id'] == '1'].iloc[0]
    missing_row = rt[rt['sub_id'] == '2'].iloc[0]

    assert null_row['contributor_last_name'] == 'NULL', (
        f"Expected literal 'NULL' surname, got {null_row['contributor_last_name']!r}"
    )
    assert pd.isna(missing_row['contributor_last_name']), (
        "Row with genuinely missing surname should be NaN"
    )


def test_default_pandas_loses_distinction():
    """Default pd.read_csv merges 'NULL' and empty; fails if pandas ever changes this."""
    df = _make_sample()
    rt = _roundtrip(df, lambda p: pd.read_csv(p, dtype={'sub_id': 'string'}, low_memory=False))

    null_row = rt[rt['sub_id'] == '1'].iloc[0]
    assert pd.isna(null_row['contributor_last_name']), (
        "Regression check: default pd.read_csv no longer coerces 'NULL' to "
        "NaN — the fec.io helper may no longer be needed, revisit."
    )


def test_quality_gate_catches_erased_null():
    """run_quality_gates must fail when 'NULL, JAMES' has empty surname."""
    from fec.cleaning.quality import run_quality_gates

    bug = pd.DataFrame([
        {'sub_id': '1', 'contributor_name': 'NULL, JAMES',
         'contributor_first_name': 'JAMES', 'contributor_last_name': None},
    ])
    q = run_quality_gates(bug)
    assert not q['checks']['null_surname_preserved']['passed']
    assert q['checks']['null_surname_preserved']['count'] == 1


def test_quality_gate_passes_with_literal_null():
    from fec.cleaning.quality import run_quality_gates

    good = pd.DataFrame([
        {'sub_id': '1', 'contributor_name': 'NULL, JAMES',
         'contributor_first_name': 'JAMES', 'contributor_last_name': 'NULL'},
        {'sub_id': '2', 'contributor_name': 'ANON',
         'contributor_first_name': None, 'contributor_last_name': None},
    ])
    q = run_quality_gates(good)
    assert q['checks']['null_surname_preserved']['passed']
    assert q['checks']['null_surname_preserved']['count'] == 0
