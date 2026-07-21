#!/usr/bin/env python3
"""
FEC Contributions Cleaner
=========================
Usage:
    python clean.py                                 # clean from scratch
    python clean.py --incremental                   # clean only NEW records
    python clean.py --no-fuzzy-city                 # skip auto city typo detection
    python clean.py --no-audit                      # disable audit trail outputs
    python clean.py data/contributions.csv -o out.csv
"""
import argparse
import json
import os

import pandas as pd

from fec.cleaning.pipeline import clean_and_match, clean_rows, unify_donors
from fec.cleaning.audit import write_audit
from fec.cleaning.quality import run_quality_gates, build_outlier_report, save_report
from fec.io import read_pipeline_csv

from fec.log import get_logger, setup_logging

logger = get_logger(__name__)


def main():
    from fec import env
    env.load_env()   # make FEC_API_KEY etc. available to the cleaning steps
    args = _parse_args()
    setup_logging(level="INFO")
    audit_enabled = not args.no_audit

    out_dir = os.path.dirname(args.output) or '.'
    os.makedirs(out_dir, exist_ok=True)
    tracker_path = os.path.join(out_dir, 'cleaned_ids.csv')

    logger.info('=' * 55)
    logger.info('  FEC Contributions Cleaner')
    logger.info('=' * 55)

    # ── Read ──
    logger.info("\n  Reading: %s", args.input)
    df = read_pipeline_csv(args.input)
    logger.info("  %s total rows, %d columns", f"{len(df):,}", len(df.columns))

    # Raw names for the JR/SR over-merge guard; snapshot before the incremental
    # filter (full runs use the identical snapshot clean_and_match builds itself).
    raw_names = None
    if args.incremental and {'sub_id', 'contributor_name'}.issubset(df.columns):
        raw_names = dict(zip(df['sub_id'].astype(str), df['contributor_name'].astype(str)))

    # ── Incremental: filter to new records only ──
    n_new = len(df)
    if args.incremental:
        if os.path.exists(tracker_path) and not os.path.exists(args.output):
            raise SystemExit(
                f"--incremental: tracker {tracker_path} exists but output "
                f"{args.output} is missing. Restore the output, or delete the "
                "tracker to force a full re-clean.")
        df, n_new = _filter_new_records(df, tracker_path)
        if n_new == 0:
            logger.info("\n  No new records to process. Everything is up to date.")
            logger.info('=' * 55)
            return

    # ── Clean (full pipeline: clean → enhance → match → post-merge) ──
    logger.info("\n── Cleaning (%s records) ──", f"{n_new:,}")

    # Map original row index for auditability
    orig_map = df[['sub_id']].copy()
    orig_map['row_index'] = orig_map.index.astype(int)

    if args.incremental and os.path.exists(args.output):
        df_clean, missing, enh_audit = clean_rows(
            df,
            fuzzy_city=not args.no_fuzzy_city,
            out_dir=out_dir,
            audit=audit_enabled,
        )
        # unify_donors must see ALL rows, or an existing donor gets a second key.
        existing = read_pipeline_csv(args.output)
        combined = _merge_incremental(existing, df_clean)
        logger.info("\n  Combined for matching: %s existing + %s new = %s rows",
                    f"{len(existing):,}", f"{len(df_clean):,}", f"{len(combined):,}")
        combined = unify_donors(combined, out_dir=out_dir, raw_names=raw_names)
        n_kept = _restore_prior_donor_keys(combined, existing)
        if n_kept:
            logger.info("  Kept prior donor_key on %s rows", f"{n_kept:,}")
        # this batch's rows, for the audit trail + tracker
        batch_ids = set(df['sub_id'].astype(str))
        df_clean = combined[combined['sub_id'].astype(str).isin(batch_ids)].copy()
        full_df = _drop_internal_cols(combined).copy()
    else:
        df_clean, missing, enh_audit = clean_and_match(
            df,
            fuzzy_city=not args.no_fuzzy_city,
            out_dir=out_dir,
            audit=audit_enabled,
        )
        full_df = _drop_internal_cols(df_clean).copy()

    # ── Guard: every committee must be known (else loader silently drops it) ──
    _assert_known_committees(full_df)

    # Ensure ZIP is properly formatted as zero-padded 5-digit string —
    # pandas concat/merge can silently convert string ZIPs to float64,
    # losing leading zeros (06880 → 6880.0). One call, right before save.
    _ensure_zip_format(full_df)
    full_df.to_csv(args.output, index=False)

    _update_tracker(df_clean, tracker_path)

    # ── Audit trail ──
    if audit_enabled:
        write_audit(df, df_clean, orig_map, out_dir, enh_audit=enh_audit)

    # ── Reports ──
    save_report(missing, out_dir, 'missing_report')

    quality = run_quality_gates(full_df)
    with open(os.path.join(out_dir, 'quality_gates.json'), 'w') as f:
        json.dump(quality, f, indent=2)

    outlier = build_outlier_report(full_df)
    with open(os.path.join(out_dir, 'outlier_report.json'), 'w') as f:
        json.dump(outlier, f, indent=2)

    # ── Proactive quality scan (detect-only — surfaces NEW classes of junk
    #    each pull: employer abbreviations, near-duplicate firms, name/address
    #    drift). Writes data/quality_scan.json for human triage; changes nothing.
    qscan = _write_quality_scan(args.output, out_dir)
    logger.info("  Quality scan: %s employers w/ abbrev, %s near-dup groups, "
                "%s name-drift rows, %s addr-variant groups",
                f"{qscan['employer_abbreviations']['distinct_employers_flagged']:,}",
                f"{qscan['employer_near_duplicates']['groups']:,}",
                f"{qscan['name_composite_drift']['rows']:,}",
                f"{qscan['address_order_variants']['groups']:,}")

    # ── Summary ──
    _print_summary(full_df, df_clean, quality, args)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _drop_internal_cols(df):
    """Drop columns not in the public output: '_'-prefixed audit columns and
    INTERNAL_OUTPUT_COLUMNS (working flags re-created downstream by the
    enhancement / safety-net steps)."""
    from fec.config import INTERNAL_OUTPUT_COLUMNS
    internal = [c for c in df.columns if c.startswith('_')]
    internal += [c for c in INTERNAL_OUTPUT_COLUMNS if c in df.columns]
    return df.drop(columns=internal, errors='ignore')


def _restore_prior_donor_keys(df, existing):
    """Keep each existing donor's first-assigned key. Re-matching the
    canonicalized output can pick a different cluster root (a different key
    hash), which would orphan curated donor_dedup_merges and DB references."""
    if 'donor_key' not in existing.columns or 'donor_key' not in df.columns:
        return 0
    prior = dict(zip(existing['sub_id'].astype(str), existing['donor_key']))
    prior_of_row = df['sub_id'].astype(str).map(prior)
    remap = {}
    for new_key, grp in prior_of_row.groupby(df['donor_key']):
        known = grp.dropna()
        if len(known):
            best = known.mode().iat[0]
            if best != new_key:
                remap[new_key] = best
    if not remap:
        return 0
    mask = df['donor_key'].isin(remap)
    df.loc[mask, 'donor_key'] = df.loc[mask, 'donor_key'].map(remap)
    return int(mask.sum())


def _merge_incremental(existing, new_rows):
    """Combine the existing output with newly cleaned rows, deduping on sub_id
    (a run that crashed before updating the tracker re-cleans those rows;
    keep='last' takes the fresh version)."""
    # existing file stores 'YYYY-MM-DD' strings; datetime64 would save with a time tail
    d = new_rows.get('contribution_receipt_date')
    if d is not None and pd.api.types.is_datetime64_any_dtype(d):
        new_rows = new_rows.copy()
        new_rows['contribution_receipt_date'] = d.dt.strftime('%Y-%m-%d')

    full_df = pd.concat([existing, new_rows], ignore_index=True)
    n_dup = int(full_df['sub_id'].duplicated().sum())
    if n_dup:
        full_df = full_df.drop_duplicates(subset='sub_id', keep='last',
                                          ignore_index=True)
        logger.info("  Dropped %s duplicate sub_ids (prior interrupted run)", f"{n_dup:,}")
    return full_df


def _write_quality_scan(output_path, out_dir):
    """Run the quality scanner on the written output → data/quality_scan.json
    (detect-only, changes nothing). Reads with na_filter off because the
    scanners expect '' not NaN."""
    from fec.cleaning.quality_scan import scan as run_quality_scan
    scan_df = pd.read_csv(output_path, dtype=str, keep_default_na=False, na_filter=False)
    report = run_quality_scan(scan_df)
    with open(os.path.join(out_dir, 'quality_scan.json'), 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return report


def _assert_known_committees(df):
    """Abort if any recipient_committee is not a committee in committees.csv.

    clean() falls back to the raw FEC number for an unrecognized committee_id;
    the loader maps committee_short→id, so that raw number matches nothing and
    EVERY such contribution is SILENTLY DROPPED at load (only a buried warning).
    Failing loudly here forces the committees.csv row to be added first — the
    documented step for every newly-pulled committee.
    """
    if 'recipient_committee' not in df.columns:
        return
    from fec.committees import load_committees
    known = {r["committee_short"] for r in load_committees() if r.get("committee_short")}
    rc = df['recipient_committee']
    unknown = rc[rc.notna() & ~rc.isin(known)]
    if len(unknown):
        logger.error("\n" + "!" * 55)
        logger.error("  ABORT — %s row(s) name a committee not in committees.csv:", f"{len(unknown):,}")
        for cid, n in unknown.value_counts().items():
            logger.error("    %-14s %s rows  -> would be SILENTLY DROPPED at load", cid, f"{n:,}")
        logger.error("  Add a row for each to data/database/committees.csv, then re-run.")
        logger.error("!" * 55 + "\n")
        raise SystemExit(1)


def _ensure_zip_format(df):
    """Ensure contributor_zip is a zero-padded 5-digit string.

    Pandas operations (concat, merge, groupby, etc.) can silently
    convert string columns to float64 when NaN values are present.
    This loses leading zeros: '06880' → 6880.0 → '6880'.

    Must be called right before to_csv() to guarantee output format.
    """
    if 'contributor_zip' not in df.columns:
        return

    z = df['contributor_zip']

    # If already clean strings, skip expensive conversion
    if z.dtype == 'object' or z.dtype.name == 'string':
        # Still might have '6880.0' from a prior float conversion
        needs_fix = z.notna() & ~z.astype(str).str.match(r'^\d{5}$', na=False)
        if not needs_fix.any():
            return

    # Convert: float/int → string → strip '.0' → zero-pad to 5 digits
    cleaned = (
        z.astype('string')
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)   # 14564.0 → 14564
        .str.replace(r'[^\d]', '', regex=True)   # strip any non-digits
    )
    # Zero-pad and take first 5 digits
    cleaned = cleaned.where(cleaned.isna() | cleaned.eq(''), 
                            cleaned.str.zfill(5).str[:5])
    # Validate: null out garbage
    invalid = cleaned.notna() & (
        ~cleaned.str.match(r'^\d{5}$', na=False) | cleaned.eq('00000')
    )
    cleaned[invalid] = pd.NA

    df['contributor_zip'] = cleaned


def _parse_args():
    p = argparse.ArgumentParser(description='FEC Contributions Cleaner')
    p.add_argument('input', nargs='?', default='data/contributions.csv')
    p.add_argument('-o', '--output', default='data/contributions_cleaned.csv')
    p.add_argument('--incremental', action='store_true',
                   help='Clean only NEW records (tracked by sub_id). Donor '
                        'matching still runs on existing output + new rows '
                        'combined, and the whole output file is rewritten, '
                        'so donor keys stay consistent.')
    p.add_argument('--no-fuzzy-city', action='store_true',
                   help='Skip auto fuzzy city typo detection')
    p.add_argument('--no-audit', action='store_true',
                   help='Disable audit trail outputs (audit_changes.* + amount_flags.csv)')
    return p.parse_args()


def _filter_new_records(df, tracker_path):
    """Filter df to only records not yet cleaned. Returns (df, n_new)."""
    if not os.path.exists(tracker_path):
        logger.info("  First run — no tracker yet. Cleaning all records.")
        return df, len(df)

    cleaned_ids = set(
        pd.read_csv(tracker_path, dtype={'sub_id': 'string'},
                    keep_default_na=False, na_values=[''])['sub_id'].tolist()
    )
    before = len(df)
    df = df[~df['sub_id'].isin(cleaned_ids)].copy()
    n_new = len(df)

    logger.info("  Tracker: %s already cleaned", f"{len(cleaned_ids):,}")
    logger.info("  New records: %s (of %s total)", f"{n_new:,}", f"{before:,}")
    return df, n_new


def _update_tracker(df_clean, tracker_path):
    """Append newly cleaned sub_ids to tracker file."""
    new_ids = df_clean[['sub_id']].copy()

    if os.path.exists(tracker_path):
        existing = pd.read_csv(tracker_path, dtype={'sub_id': 'string'},
                               keep_default_na=False, na_values=[''])
        combined = pd.concat([existing, new_ids], ignore_index=True).drop_duplicates()
        combined.to_csv(tracker_path, index=False)
    else:
        new_ids.to_csv(tracker_path, index=False)


def _print_summary(full, df_clean, quality, args):
    """Print final summary to console."""
    individuals = full[full['entity_type'] == 'INDIVIDUAL']
    committees  = full[full['entity_type'] == 'COMMITTEE/PAC']
    mode = 'INCREMENTAL' if args.incremental else 'FULL'

    logger.info("\n" + '=' * 55)
    logger.info("  Mode:   %s", mode)
    logger.info("  Output: %s", args.output)
    logger.info("  Rows:   %s (%s individuals, %s committees)", f"{len(full):,}", f"{len(individuals):,}", f"{len(committees):,}")

    if args.incremental:
        logger.info("  New:    %s records added this run", f"{len(df_clean):,}")

    if quality['issues']:
        logger.info("  Quality issues: %s", quality['issues'])
    else:
        logger.info("  Quality gates: ALL PASSED ✓")

    if 'occupation_category' in individuals.columns and len(individuals) > 0:
        logger.info("\n  Top occupation categories:")
        for cat, cnt in individuals['occupation_category'].value_counts().head(10).items():
            logger.info("    %-30s %7s", cat, f"{cnt:,}")

    logger.info('=' * 55)


if __name__ == '__main__':
    main()
