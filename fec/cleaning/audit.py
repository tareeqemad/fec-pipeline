"""Audit trail of cleaning changes: audit_changes.csv, audit_changes.jsonl, audit_report.html, amount_flags.csv."""
import gc
import html as html_lib
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd


def write_audit(df_before, df_after, orig_map, out_dir, enh_audit=None):
    """Compare before/after and write the audit files (reclassification, street/email handling, name fixes, enhancement steps)."""
    if 'sub_id' not in df_before.columns or 'sub_id' not in df_after.columns:
        return

    before = df_before.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')
    after  = df_after.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')
    row_idx = orig_map.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')['row_index']

    records = []

    def _val(df, col, sid):
        if col not in df.columns:
            return ''
        value = df.at[sid, col] if sid in df.index else ''
        return '' if pd.isna(value) else str(value)

    def _add(sid, field, step, reason, evidence=None):
        rec = {
            'sub_id': str(sid),
            'row_index': int(row_idx.get(sid)) if sid in row_idx.index and pd.notna(row_idx.get(sid)) else None,
            'field': field,
            'before': _val(before, field, sid),
            'after':  _val(after,  field, sid),
            'step': step,
            'reason': reason,
            'evidence': evidence,
        }
        if rec['before'] != rec['after']:
            records.append(rec)

    # 1. reclassification
    if '_reclass_reason' in after.columns:
        reclass_reasons = after['_reclass_reason'].astype('string')
        for sid in after.index[reclass_reasons.notna()]:
            reason = str(reclass_reasons.loc[sid])
            for field in ('is_individual', 'entity_type'):
                if field in before.columns and field in after.columns:
                    _add(sid, field, step='reclassify', reason=reason)

    # 2. street / email handling
    if '_street_email_in_s1' in after.columns:
        flag = after['_street_email_in_s1'].fillna(False).astype(bool)
        swapped = after.get('_street_swapped_from_s2', pd.Series(False, index=after.index)).fillna(False).astype(bool)
        nulled  = after.get('_street_nulled_email',    pd.Series(False, index=after.index)).fillna(False).astype(bool)

        for sid in after.index[flag]:
            if bool(swapped.loc[sid]):
                reason = 'street_swap_due_to_email_in_street1'
            elif bool(nulled.loc[sid]):
                reason = 'street_nulled_due_to_email_in_street1'
            else:
                reason = 'street_email_in_street1'
            for field in ('contributor_street_1', 'contributor_street_2'):
                if field in before.columns and field in after.columns:
                    _add(sid, field, step='clean_streets', reason=reason)

    # 3. garbled first name fixes
    if '_garbled_before' in after.columns:
        garbled = after['_garbled_before'].astype('string')
        for sid in after.index[garbled.notna() & (garbled != '<NA>')]:
            old_first = str(garbled.loc[sid])
            for field in ('contributor_first_name', 'contributor_name'):
                _add(sid, field, step='garbled_name_fix',
                     reason='keyboard_error',
                     evidence=f'was: {old_first}')

    # 4. enhancement audit records
    if enh_audit:
        for record in enh_audit:
            sid = record.get('sub_id', '')
            record['row_index'] = int(row_idx.get(sid)) if sid in row_idx.index and pd.notna(row_idx.get(sid)) else None
            if 'evidence' not in record:
                record['evidence'] = None
            records.append(record)

    _write_csv_jsonl(records, out_dir)
    _write_amount_flags(after, row_idx, out_dir)
    _write_html(records, out_dir)

    # release the indexed copies before collecting (plain rebinding, not `del`:
    # the nested _val/_add close over `before`/`after`/`records`)
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
