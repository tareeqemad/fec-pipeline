"""
cleaning/audit.py — Audit trail: what changed, why, and evidence.

Generates:
  - audit_changes.csv   (one row per field change)
  - audit_changes.jsonl  (same data, JSON Lines format)
  - audit_report.html    (manager-friendly visual diff)
  - amount_flags.csv     (negative/zero amounts — informational, not mutated)
"""
import gc
import html as html_lib
import json
import os
from collections import defaultdict

import numpy as np
import pandas as pd


def write_audit(df_before, df_after, orig_map, out_dir, enh_audit=None):
    """
    Compare before/after and write audit files.

    Logs changes from:
      1. Reclassification (committee ↔ individual)
      2. Street/email handling
      3. In-file street imputation
      4. Enhancement steps (passed via enh_audit)
    """
    if 'sub_id' not in df_before.columns or 'sub_id' not in df_after.columns:
        return

    before = df_before.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')
    after  = df_after.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')
    row_idx = orig_map.drop_duplicates(subset='sub_id', keep='first').set_index('sub_id')['row_index']

    records = []

    def _val(df, col, sid):
        if col not in df.columns:
            return ''
        v = df.at[sid, col] if sid in df.index else ''
        return '' if pd.isna(v) else str(v)

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

    # ── 1. Reclassification ──
    if '_reclass_reason' in after.columns:
        rr = after['_reclass_reason'].astype('string')
        for sid in after.index[rr.notna()]:
            reason = str(rr.loc[sid])
            for fld in ('is_individual', 'entity_type'):
                if fld in before.columns and fld in after.columns:
                    _add(sid, fld, step='reclassify', reason=reason)

    # ── 2. Street / email handling ──
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
            for fld in ('contributor_street_1', 'contributor_street_2'):
                if fld in before.columns and fld in after.columns:
                    _add(sid, fld, step='clean_streets', reason=reason)

    # ── 3. Garbled first name fixes ──
    if '_garbled_before' in after.columns:
        gb = after['_garbled_before'].astype('string')
        for sid in after.index[gb.notna() & (gb != '<NA>')]:
            old_first = str(gb.loc[sid])
            for fld in ('contributor_first_name', 'contributor_name'):
                _add(sid, fld, step='garbled_name_fix',
                     reason='keyboard_error',
                     evidence=f'was: {old_first}')

    # ── 4. Enhancement audit records ──
    if enh_audit:
        for rec in enh_audit:
            sid = rec.get('sub_id', '')
            rec['row_index'] = int(row_idx.get(sid)) if sid in row_idx.index and pd.notna(row_idx.get(sid)) else None
            if 'evidence' not in rec:
                rec['evidence'] = None
            records.append(rec)

    # ── Write files ──
    _write_csv_jsonl(records, out_dir)
    _write_amount_flags(after, row_idx, out_dir)
    _write_html(records, out_dir)

    del records, before, after
    gc.collect()


# ─── Helpers ────────────────────────────────────────────────


def _write_csv_jsonl(records, out_dir):
    """Write audit_changes.csv and audit_changes.jsonl."""
    cols = ['sub_id', 'row_index', 'field', 'before', 'after', 'step', 'reason', 'evidence']
    pd.DataFrame.from_records(records, columns=cols).to_csv(
        os.path.join(out_dir, 'audit_changes.csv'), index=False)

    with open(os.path.join(out_dir, 'audit_changes.jsonl'), 'w', encoding='utf-8') as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def _write_amount_flags(df, row_idx, out_dir):
    """Flag negative/zero amounts (informational only — does NOT change data)."""
    path = os.path.join(out_dir, 'amount_flags.csv')
    empty_cols = ['sub_id', 'row_index', 'contribution_receipt_amount', 'flag', 'evidence']

    if 'contribution_receipt_amount' not in df.columns:
        pd.DataFrame(columns=empty_cols).to_csv(path, index=False)
        return

    amt = pd.to_numeric(df['contribution_receipt_amount'], errors='coerce')
    mask = amt.lt(0) | amt.eq(0)
    if not mask.any():
        pd.DataFrame(columns=empty_cols).to_csv(path, index=False)
        return

    flags = pd.DataFrame({
        'sub_id': df.index[mask],
        'row_index': row_idx.reindex(df.index[mask]).astype('Int64').values,
        'contribution_receipt_amount': amt[mask].values,
        'flag': np.where(amt[mask].lt(0), 'refund_or_adjustment', 'zero_amount_void_or_refund'),
        'evidence': np.where(amt[mask].lt(0), 'negative_amount', 'zero_amount'),
    })
    flags.to_csv(path, index=False)


def _write_html(records, out_dir):
    """Generate a visual HTML audit report."""
    path = os.path.join(out_dir, 'audit_report.html')

    if not records:
        with open(path, 'w') as f:
            f.write('<!DOCTYPE html><html><body><h1>Audit Report</h1>'
                    '<p>No changes recorded.</p></body></html>')
        return

    by_id = defaultdict(list)
    step_counts = defaultdict(int)
    for r in records:
        by_id[r['sub_id']].append(r)
        step_counts[r['step']] += 1

    def e(s):
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
             f'<p><strong>By step:</strong> {e(", ".join(f"{k}: {v}" for k,v in sorted(step_counts.items())))}</p></div>']

    sorted_ids = sorted(by_id, key=lambda x: (by_id[x][0].get('row_index') is None,
                                                by_id[x][0].get('row_index') or 0))
    for sid in sorted_ids:
        recs = by_id[sid]
        idx = recs[0].get('row_index')
        label = f' (row {idx})' if idx is not None else ''
        parts.append(f'<div class="record"><h2>Record: {e(sid)}{label}</h2>')
        parts.append('<table><thead><tr><th>Field</th><th>Before</th><th>After</th><th>Reason</th></tr></thead><tbody>')
        for r in recs:
            ev = f' <span class="evidence">[{e(r.get("evidence",""))}]</span>' if r.get('evidence') else ''
            parts.append(
                f'<tr><td>{e(r["field"])}</td><td class="before">{e(r["before"])}</td>'
                f'<td class="after">{e(r["after"])}</td>'
                f'<td class="reason">{e(r["step"])}: {e(r.get("reason",""))}{ev}</td></tr>')
        parts.append('</tbody></table></div>')

    parts.append('</body></html>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(parts))
