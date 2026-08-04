"""The clean command: python clean.py [input] [-o out] [--incremental] [--no-fuzzy-city] [--no-audit]."""
import argparse
import json
import os

import pandas as pd

from fec.cleaning.audit import write_audit
from fec.cleaning.pipeline import clean_and_match, clean_rows, unify_donors
from fec.cleaning.quality import run_quality_gates, build_outlier_report, save_report
from fec.io import read_pipeline_csv
from fec.log import get_logger, setup_logging

logger = get_logger(__name__)


def _drop_internal_cols(df):
    """Drop '_'-prefixed audit columns and INTERNAL_OUTPUT_COLUMNS (working flags re-created downstream) from the public output."""
    from fec.config import INTERNAL_OUTPUT_COLUMNS
    internal = [column for column in df.columns if column.startswith('_')]
    internal += [column for column in INTERNAL_OUTPUT_COLUMNS if column in df.columns]
    return df.drop(columns=internal)


def _restore_prior_donor_keys(df, existing):
    """Pin existing donors to their first-assigned key; re-matching canonicalized output can pick a different cluster root and orphan curated donor_dedup_merges."""
    prior = dict(zip(existing['sub_id'].astype(str), existing['donor_key']))
    prior_of_row = df['sub_id'].astype(str).map(prior)
    remap = {}
    for new_key, group in prior_of_row.groupby(df['donor_key']):
        known = group.dropna()
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
    """Concat existing output with newly cleaned rows, deduping on sub_id (keep='last' takes the fresh version after an interrupted run)."""
    # existing file stores 'YYYY-MM-DD' strings; datetime64 would save with a time tail
    dates = new_rows.get('contribution_receipt_date')
    if dates is not None and pd.api.types.is_datetime64_any_dtype(dates):
        new_rows = new_rows.copy()
        new_rows['contribution_receipt_date'] = dates.dt.strftime('%Y-%m-%d')

    full_df = pd.concat([existing, new_rows], ignore_index=True)
    n_dup = int(full_df['sub_id'].duplicated().sum())
    if n_dup:
        full_df = full_df.drop_duplicates(subset='sub_id', keep='last',
                                          ignore_index=True)
        logger.info("  Dropped %s duplicate sub_ids (prior interrupted run)", f"{n_dup:,}")
    return full_df


def _write_quality_scan(output_path, out_dir):
    """Run the detect-only quality scanner on the written output -> quality_scan.json; reads with na_filter off (scanners expect '' not NaN)."""
    from fec.cleaning.quality_scan import scan as run_quality_scan
    scan_df = pd.read_csv(output_path, dtype=str, keep_default_na=False, na_filter=False)
    report = run_quality_scan(scan_df)
    with open(os.path.join(out_dir, 'quality_scan.json'), 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return report


def _assert_known_committees(df):
    """Abort if a recipient_committee is missing from committees.csv - the loader would silently drop every such row at load."""
    from fec.committees import load_committees
    known = {row["committee_short"] for row in load_committees() if row.get("committee_short")}
    recipients = df['recipient_committee']
    unknown = recipients[recipients.notna() & ~recipients.isin(known)]
    if len(unknown):
        logger.error("  ABORT - %s row(s) name a committee not in committees.csv:", f"{len(unknown):,}")
        for committee, count in unknown.value_counts().items():
            logger.error("    %-14s %s rows  -> would be SILENTLY DROPPED at load", committee, f"{count:,}")
        logger.error("  Add a row for each to data/database/committees.csv, then re-run.")
        raise SystemExit(1)


def _ensure_zip_format(df):
    """Force contributor_zip to a zero-padded 5-digit string right before to_csv (pandas ops can float-ify it, losing leading zeros)."""
    zips = df['contributor_zip']

    if zips.dtype.name in ('object', 'string'):
        # may still hold '6880.0' from a prior float conversion
        already_clean = zips.isna() | zips.astype(str).str.match(r'^\d{5}$', na=False)
        if already_clean.all():
            return

    cleaned = (
        zips.astype('string')
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)   # 14564.0 -> 14564
        .str.replace(r'[^\d]', '', regex=True)
        .str.zfill(5).str[:5]                   # NA stays NA; '' becomes 00000, nulled below
    )
    invalid = cleaned.notna() & (
        ~cleaned.str.match(r'^\d{5}$', na=False) | cleaned.eq('00000')
    )
    cleaned[invalid] = pd.NA

    df['contributor_zip'] = cleaned


def _parse_args():
    parser = argparse.ArgumentParser(description='FEC Contributions Cleaner')
    parser.add_argument('input', nargs='?', default='data/contributions.csv')
    parser.add_argument('-o', '--output', default='data/contributions_cleaned.csv')
    parser.add_argument('--incremental', action='store_true',
                        help='Clean only NEW records (tracked by sub_id). Donor '
                             'matching still runs on existing output + new rows '
                             'combined, and the whole output file is rewritten, '
                             'so donor keys stay consistent.')
    parser.add_argument('--no-fuzzy-city', action='store_true',
                        help='Skip auto fuzzy city typo detection')
    parser.add_argument('--no-audit', action='store_true',
                        help='Disable audit trail outputs (audit_changes.* + amount_flags.csv)')
    return parser.parse_args()


def _filter_new_records(df, tracker_path):
    """Filter df to records not yet cleaned. Returns (df, n_new)."""
    if not os.path.exists(tracker_path):
        logger.info("  First run - no tracker yet. Cleaning all records.")
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
    """Append genuinely new sub_ids to the tracker file."""
    new_ids = df_clean[['sub_id']].astype({'sub_id': 'string'})

    if not os.path.exists(tracker_path):
        new_ids.drop_duplicates().to_csv(tracker_path, index=False)
        return

    existing = pd.read_csv(
        tracker_path,
        dtype={'sub_id': 'string'},
        keep_default_na=False,
        na_values=[''],
    )
    new_ids = new_ids[~new_ids['sub_id'].isin(existing['sub_id'])]
    if new_ids.empty:
        return

    pd.concat([existing, new_ids], ignore_index=True).to_csv(tracker_path, index=False)


def _print_summary(full, df_clean, quality, args):
    """Print the final run summary."""
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
        logger.info("  Quality gates: ALL PASSED")

    if len(individuals) > 0:
        logger.info("\n  Top occupation categories:")
        for cat, cnt in individuals['occupation_category'].value_counts().head(10).items():
            logger.info("    %-30s %7s", cat, f"{cnt:,}")

    logger.info('=' * 55)


def main():
    from fec import env
    env.load_env()   # make FEC_API_KEY etc. available to the cleaning steps
    args = _parse_args()
    setup_logging()
    audit_enabled = not args.no_audit

    out_dir = os.path.dirname(args.output) or '.'
    os.makedirs(out_dir, exist_ok=True)
    tracker_path = os.path.join(out_dir, 'cleaned_ids.csv')

    logger.info('=' * 55)
    logger.info('  FEC Contributions Cleaner')
    logger.info('=' * 55)

    logger.info("\n  Reading: %s", args.input)
    df = read_pipeline_csv(args.input)
    logger.info("  %s total rows, %d columns", f"{len(df):,}", len(df.columns))

    # JR/SR over-merge guard needs raw names snapshotted BEFORE the incremental filter
    raw_names = None
    if args.incremental and {'sub_id', 'contributor_name'}.issubset(df.columns):
        raw_names = dict(zip(df['sub_id'].astype(str), df['contributor_name'].astype(str)))

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

    logger.info("\n-- Cleaning (%s records) --", f"{n_new:,}")

    # original row index, for the audit trail
    orig_map = df[['sub_id']].copy()
    orig_map['row_index'] = orig_map.index.astype(int)

    if args.incremental and os.path.exists(args.output):
        df_clean, missing, enh_audit = clean_rows(
            df,
            fuzzy_city=not args.no_fuzzy_city,
            out_dir=out_dir,
            audit=audit_enabled,
        )
        # unify_donors must see ALL rows, or an existing donor gets a second key
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

    # a committee missing from committees.csv would be silently dropped at load
    _assert_known_committees(full_df)

    # concat/merge can float-ify string ZIPs (06880 -> 6880.0); fix right before save
    _ensure_zip_format(full_df)
    full_df.to_csv(args.output, index=False)

    _update_tracker(df_clean, tracker_path)

    if audit_enabled:
        write_audit(df, df_clean, orig_map, out_dir, enh_audit=enh_audit)

    save_report(missing, out_dir, 'missing_report')

    quality = run_quality_gates(full_df)
    with open(os.path.join(out_dir, 'quality_gates.json'), 'w') as handle:
        json.dump(quality, handle, indent=2)

    outlier = build_outlier_report(full_df)
    with open(os.path.join(out_dir, 'outlier_report.json'), 'w') as handle:
        json.dump(outlier, handle, indent=2)

    # detect-only scan for new junk classes -> data/quality_scan.json (human triage)
    qscan = _write_quality_scan(args.output, out_dir)
    logger.info("  Quality scan: %s employers w/ abbrev, %s near-dup groups, "
                "%s name-drift rows, %s addr-variant groups",
                f"{qscan['employer_abbreviations']['distinct_employers_flagged']:,}",
                f"{qscan['employer_near_duplicates']['groups']:,}",
                f"{qscan['name_composite_drift']['rows']:,}",
                f"{qscan['address_order_variants']['groups']:,}")

    _print_summary(full_df, df_clean, quality, args)
