"""Write the audit files."""
import gc
import json
import os

import numpy as np
import pandas as pd

from fec.cleaning.audit_trail import AUDITED_FIELDS, UNTRACKED_STEP, AuditTrail, summarize

CHANGE_COLUMNS = ['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason', 'source']


def write_audit(df_after, orig_map, out_dir, trail: AuditTrail):
    """Write every cleaning audit artifact."""
    after = df_after.drop_duplicates('sub_id', keep='first').set_index('sub_id')
    row_index = (
        orig_map.drop_duplicates('sub_id', keep='first')
        .set_index('sub_id')['row_index']
    )
    row_index.index = row_index.index.astype(str)

    net = trail.net_records()
    changes = _frame(net, row_index)
    changes.to_csv(os.path.join(out_dir, 'audit_changes.csv'), index=False)
    summary = {
        'changes': int(len(changes)),
        'changed_cells': int(changes[['sub_id', 'field']].drop_duplicates().shape[0]),
        'untracked_changes': sum(record['step'] == UNTRACKED_STEP for record in net),
        'steps': summarize([record for record in net if record['field'] in AUDITED_FIELDS]),
    }
    with open(os.path.join(out_dir, 'audit_summary.json'), 'w', encoding='utf-8') as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
    _write_amount_flags(after, row_index, out_dir)

    after = changes = net = None
    gc.collect()
    return summary


def _frame(records, row_index):
    frame = pd.DataFrame.from_records(records, columns=CHANGE_COLUMNS)
    frame = frame[frame['field'].isin(AUDITED_FIELDS)]
    frame['row_index'] = row_index.reindex(frame['sub_id']).to_numpy()
    frame['row_index'] = frame['row_index'].astype('Int64')
    return frame[CHANGE_COLUMNS]


def _write_amount_flags(df, row_idx, out_dir):
    """Flag negative/zero amounts (informational only; does not change data)."""
    path = os.path.join(out_dir, 'amount_flags.csv')
    empty_cols = ['sub_id', 'row_index', 'contribution_receipt_amount', 'flag', 'evidence']

    if 'contribution_receipt_amount' not in df.columns:
        pd.DataFrame(columns=empty_cols).to_csv(path, index=False)
        return

    amounts = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')
    mask = amounts.lt(0) | amounts.eq(0)
    if not mask.any():
        pd.DataFrame(columns=empty_cols).to_csv(path, index=False)
        return

    flags = pd.DataFrame({
        'sub_id': df.index[mask],
        'row_index': row_idx.reindex(df.index[mask]).astype('Int64').values,
        'contribution_receipt_amount': amounts[mask].values,
        'flag': np.where(amounts[mask].lt(0), 'refund_or_adjustment', 'zero_amount_void_or_refund'),
        'evidence': np.where(amounts[mask].lt(0), 'negative_amount', 'zero_amount'),
    })
    flags.to_csv(path, index=False)
