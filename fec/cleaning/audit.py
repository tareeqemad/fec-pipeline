"""Write the cleaning audit CSV files."""
import gc
import os

import numpy as np
import pandas as pd


def _collect_reclassifications(before, after, add_change):
    if '_reclass_reason' not in after.columns:
        return

    reasons = after['_reclass_reason'].astype('string')
    for sub_id in after.index[reasons.notna()]:
        reason = str(reasons.loc[sub_id])
        for field in ('is_individual', 'entity_type'):
            if field in before.columns and field in after.columns:
                add_change(sub_id, field, 'reclassify', reason)


def _collect_street_changes(before, after, add_change):
    if '_street_email_in_s1' not in after.columns:
        return

    flagged = after['_street_email_in_s1'].fillna(False).astype(bool)
    swapped = after['_street_swapped_from_s2'].fillna(False).astype(bool)
    nulled = after['_street_nulled_email'].fillna(False).astype(bool)

    for sub_id in after.index[flagged]:
        if bool(swapped.loc[sub_id]):
            reason = 'street_swap_due_to_email_in_street1'
        elif bool(nulled.loc[sub_id]):
            reason = 'street_nulled_due_to_email_in_street1'
        else:
            reason = 'street_email_in_street1'

        for field in ('contributor_street_1', 'contributor_street_2'):
            if field in before.columns and field in after.columns:
                add_change(sub_id, field, 'clean_streets', reason)


def _collect_garbled_names(after, add_change):
    if '_garbled_before' not in after.columns:
        return

    garbled = after['_garbled_before'].astype('string')
    changed = garbled.notna() & (garbled != '<NA>')
    for sub_id in after.index[changed]:
        evidence = f'was: {garbled.loc[sub_id]}'
        for field in ('contributor_first_name', 'contributor_name'):
            add_change(
                sub_id, field, 'garbled_name_fix',
                'keyboard_error', evidence,
            )


def _append_rule_changes(records, changes, row_index):
    for record in changes or ():
        sub_id = record.get('sub_id', '')
        value = row_index.get(sub_id)
        record['row_index'] = int(value) if pd.notna(value) else None
        record.setdefault('evidence', None)
        records.append(record)


def write_audit(df_before, df_after, orig_map, out_dir, rule_audit=None):
    """Compare before/after and write every cleaning audit artifact."""
    if 'sub_id' not in df_before.columns or 'sub_id' not in df_after.columns:
        return

    before = df_before.drop_duplicates('sub_id', keep='first').set_index('sub_id')
    after = df_after.drop_duplicates('sub_id', keep='first').set_index('sub_id')
    row_index = (
        orig_map.drop_duplicates('sub_id', keep='first')
        .set_index('sub_id')['row_index']
    )
    records = []

    def value(frame, field, sub_id):
        if field not in frame.columns or sub_id not in frame.index:
            return ''
        cell = frame.at[sub_id, field]
        return '' if pd.isna(cell) else str(cell)

    def add_change(sub_id, field, step, reason, evidence=None):
        before_value = value(before, field, sub_id)
        after_value = value(after, field, sub_id)
        if before_value == after_value:
            return

        row_number = row_index.get(sub_id)
        records.append({
            'sub_id': str(sub_id),
            'row_index': int(row_number) if pd.notna(row_number) else None,
            'field': field,
            'before': before_value,
            'after': after_value,
            'step': step,
            'reason': reason,
            'evidence': evidence,
        })

    _collect_reclassifications(before, after, add_change)
    _collect_street_changes(before, after, add_change)
    _collect_garbled_names(after, add_change)
    _append_rule_changes(records, rule_audit, row_index)

    _write_changes(records, out_dir)
    _write_amount_flags(after, row_index, out_dir)

    records = before = after = None
    gc.collect()


def _write_changes(records, out_dir):
    cols = ['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason', 'evidence']
    pd.DataFrame.from_records(records, columns=cols).to_csv(
        os.path.join(out_dir, 'audit_changes.csv'), index=False)

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
