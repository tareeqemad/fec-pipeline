import pandas as pd

import fec.cleaning.audit as audit


def test_locked_html_report_does_not_fail_the_clean_run(tmp_path, monkeypatch):
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

    def locked_report(*_args, **_kwargs):
        raise OSError('report is open in a browser')

    monkeypatch.setattr(audit, '_write_html', locked_report)
    audit.write_audit(
        before,
        after,
        original_rows,
        tmp_path,
        enh_audit=[{'sub_id': '1', 'field': 'contributor_name',
                    'before': 'RAW NAME', 'after': 'CLEAN NAME',
                    'step': 'test', 'reason': 'test'}],
    )
    assert (tmp_path / 'audit_changes.csv').exists()
