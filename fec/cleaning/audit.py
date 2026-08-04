"""Audit trail of cleaning changes: audit_changes.csv, audit_changes.jsonl, audit_report.html, amount_flags.csv."""
import gc
import html as html_lib
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd

from fec.log import get_logger

logger = get_logger(__name__)


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


def _append_enhancements(records, enhancements, row_index):
    for record in enhancements or ():
        sub_id = record.get('sub_id', '')
        value = row_index.get(sub_id)
        record['row_index'] = int(value) if pd.notna(value) else None
        record.setdefault('evidence', None)
        records.append(record)


def write_audit(df_before, df_after, orig_map, out_dir, enh_audit=None):
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
    _append_enhancements(records, enh_audit, row_index)

    _write_csv_jsonl(records, out_dir)
    _write_amount_flags(after, row_index, out_dir)
    try:
        _write_html(records, out_dir)
    except OSError as error:
        logger.warning("Audit HTML skipped: %s", error)

    records = before = after = None
    gc.collect()


def _write_csv_jsonl(records, out_dir):
    cols = ['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason', 'evidence']
    pd.DataFrame.from_records(records, columns=cols).to_csv(
        os.path.join(out_dir, 'audit_changes.csv'), index=False)

    with open(os.path.join(out_dir, 'audit_changes.jsonl'), 'w', encoding='utf-8') as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + '\n')


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


def _write_html(records, out_dir):
    path = os.path.join(out_dir, 'audit_report.html')

    if not records:
        with open(path, 'w') as handle:
            handle.write('<!DOCTYPE html><html><body><h1>Audit Report</h1>'
                         '<p>No changes recorded.</p></body></html>')
        return

    by_id = defaultdict(list)
    step_counts = defaultdict(int)
    for record in records:
        by_id[record['sub_id']].append(record)
        step_counts[record['step']] += 1

    def escape(s):
        if s is None or (isinstance(s, float) and pd.isna(s)):
            return ''
        return html_lib.escape(str(s))

    css = ('<style>'
           'body{font-family:system-ui,sans-serif;background:#f8f9fa;padding:1rem 2rem;color:#212529}'
           'h1{font-size:1.5rem}.meta{color:#6c757d;margin-bottom:1.5rem}'
           '.summary,.record{background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:1rem 1.5rem;margin-bottom:1rem}'
           '.record h2{font-size:1rem;margin:0 0 .5rem;color:#495057}'
           'table{width:100%;border-collapse:collapse;font-size:.9rem}'
           'th,td{text-align:left;padding:.5rem .75rem;border-bottom:1px solid #dee2e6}'
           'th{background:#f8f9fa;font-weight:600}.before{background:#fff5f5}.after{background:#f0fff0}'
           '.reason{font-size:.85rem;color:#6c757d}.evidence{font-size:.8rem;color:#0d6efd}'
           '</style>')

    parts = [f'<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Audit Report</title>{css}</head><body>',
             '<h1>Audit Report — Record Changes</h1>',
             '<div class="meta">Each record shows before &rarr; after with the reason.</div>',
             f'<div class="summary"><p><strong>Total changes:</strong> {len(records)}</p>',
             f'<p><strong>Records affected:</strong> {len(by_id)}</p>',
             f'<p><strong>By step:</strong> {escape(", ".join(f"{step}: {count}" for step,count in sorted(step_counts.items())))}</p></div>']

    sorted_ids = sorted(by_id, key=lambda sub_id: (by_id[sub_id][0].get('row_index') is None,
                                                by_id[sub_id][0].get('row_index') or 0))
    for sid in sorted_ids:
        sid_records = by_id[sid]
        row_index = sid_records[0].get('row_index')
        label = f' (row {row_index})' if row_index is not None else ''
        parts.append(f'<div class="record"><h2>Record: {escape(sid)}{label}</h2>')
        parts.append('<table><thead><tr><th>Field</th><th>Before</th><th>After</th><th>Reason</th></tr></thead><tbody>')
        for record in sid_records:
            evidence_html = f' <span class="evidence">[{escape(record.get("evidence",""))}]</span>' if record.get('evidence') else ''
            parts.append(
                f'<tr><td>{escape(record["field"])}</td><td class="before">{escape(record["before"])}</td>'
                f'<td class="after">{escape(record["after"])}</td>'
                f'<td class="reason">{escape(record["step"])}: {escape(record.get("reason",""))}{evidence_html}</td></tr>')
        parts.append('</tbody></table></div>')

    parts.append('</body></html>')
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(parts))
