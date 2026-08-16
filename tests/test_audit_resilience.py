import pandas as pd

import fec.cleaning.audit as audit


def test_audit_writes_only_csv_reports(tmp_path):
    before = pd.DataFrame({
        'sub_id': ['1'],
        'contributor_name': ['RAW NAME'],
        'contribution_receipt_amount': ['100'],
    })
    after = pd.DataFrame({
        'sub_id': ['1'],
        'contributor_name': ['CLEAN NAME'],
        'contribution_receipt_amount': ['100'],
    })
    original_rows = pd.DataFrame({'sub_id': ['1'], 'row_index': [0]})

    audit.write_audit(
        before,
        after,
        original_rows,
        tmp_path,
        rule_audit=[{'sub_id': '1', 'field': 'contributor_name',
                    'before': 'RAW NAME', 'after': 'CLEAN NAME',
                    'step': 'test', 'reason': 'test'}],
    )
    assert (tmp_path / 'audit_changes.csv').exists()
    assert (tmp_path / 'amount_flags.csv').exists()
    assert not (tmp_path / 'audit_changes.jsonl').exists()
    assert not (tmp_path / 'audit_report.html').exists()
