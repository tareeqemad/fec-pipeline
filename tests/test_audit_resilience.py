import json

import pandas as pd

import fec.cleaning.audit as audit
from fec.cleaning.audit_trail import AuditTrail


def test_audit_writes_only_csv_reports(tmp_path):
    before = pd.DataFrame({
        'sub_id': ['1', '2'],
        'contributor_name': ['RAW NAME', 'SMITH, JOHN'],
        'contributor_employer': ['SELF', 'ACME'],
        'contribution_receipt_amount': ['100', '-5'],
    })
    original_rows = pd.DataFrame({'sub_id': ['1', '2'], 'row_index': [0, 1]})

    trail = AuditTrail()
    trail.start(before)
    after = before.copy()

    def step(frame):
        frame['contributor_name'] = ['CLEAN NAME', 'SMITH, JOHN']
        frame['contributor_employer'] = ['SELF-EMPLOYED', 'ACME']
        return 2

    trail.run(after, step, 'test', 'test_reason', ('contributor_name', 'contributor_employer'))
    trail.finish(after)

    audit.write_audit(after, original_rows, tmp_path, trail)

    assert (tmp_path / 'audit_changes.csv').exists()
    assert (tmp_path / 'audit_format_changes.csv').exists()
    assert (tmp_path / 'audit_summary.json').exists()
    assert (tmp_path / 'amount_flags.csv').exists()
    assert not (tmp_path / 'audit_changes.jsonl').exists()
    assert not (tmp_path / 'audit_report.html').exists()

    changes = pd.read_csv(tmp_path / 'audit_changes.csv', dtype=str, keep_default_na=False)
    assert changes.columns.tolist() == audit.CHANGE_COLUMNS
    assert changes[['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason']].values.tolist() == [
        ['1', '0', 'contributor_name', 'RAW NAME', 'CLEAN NAME', 'test', 'test_reason'],
    ]
    fmt = pd.read_csv(tmp_path / 'audit_format_changes.csv', dtype=str, keep_default_na=False)
    assert fmt[['sub_id', 'field', 'before', 'after']].values.tolist() == [
        ['1', 'contributor_employer', 'SELF', 'SELF-EMPLOYED'],
    ]
    summary = json.loads((tmp_path / 'audit_summary.json').read_text(encoding='utf-8'))
    assert summary['semantic_changes'] == 1
    assert summary['format_changes'] == 1
    assert summary['untracked_changes'] == 0
    assert summary['steps']['test'] == {'semantic': 1, 'format': 1, 'reasons': {'test_reason': 1}}
    flags = pd.read_csv(tmp_path / 'amount_flags.csv', dtype=str)
    assert flags['sub_id'].tolist() == ['2']
