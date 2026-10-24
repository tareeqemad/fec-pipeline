#!/usr/bin/env python3
"""Read, clean, check and save FEC contributions."""

import os

import pandas as pd

from fec import env
from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.cleaning.reports.audit import write_audit
from fec.cleaning.pipeline import clean_pipeline
from fec.cleaning.reports.gates import run_quality_gates
from fec.cleaning.reports.scan import scan
from fec.committees import load_committees
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.contract import check_output
from fec.env import CACHE_DIR, CLEANED_CSV, RAW_CSV, REPORTS_DIR
from fec.io import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger

logger = get_logger(__name__)


# Read, clean, check and save contributions.
def main():
    env.load_env()

    input_path = str(RAW_CSV)
    output_path = str(CLEANED_CSV)
    out_dir = str(REPORTS_DIR)
    for folder in (out_dir, CACHE_DIR, CLEANED_CSV.parent):
        os.makedirs(folder, exist_ok=True)

    logger.info('=' * 55)
    logger.info('  FEC Contributions Cleaner')
    logger.info('=' * 55)
    logger.info("\n  Reading: %s", input_path)

    df = read_pipeline_csv(input_path)
    logger.info("  %s rows, %d columns", f"{len(df):,}", len(df.columns))
    logger.info("\n-- Cleaning records --")

    original_rows = df[['sub_id']].copy()
    original_rows['row_index'] = original_rows.index.astype(int)

    cleaned, missing, trail = clean_pipeline(
        df,
        out_dir=out_dir,
        cache_dir=str(CACHE_DIR),
    )
    output = cleaned.drop(columns=[
        column for column in cleaned.columns
        if column.startswith('_') or column in INTERNAL_OUTPUT_COLUMNS
    ])

    _assert_known_committees(output)
    _ensure_zip_format(output)

    quality = _save_if_gates_pass(output, out_dir, output_path)
    _write_reports(cleaned, original_rows, missing, trail, out_dir, output_path)
    _print_summary(output, quality, output_path)


# Write the quality scan for the saved CSV.
def _write_quality_scan(output_path, out_dir):
    df = pd.read_csv(
        output_path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    report = scan(df)
    path = os.path.join(out_dir, 'quality_scan.json')
    write_json_atomic(path, report, indent=2, ensure_ascii=False)
    return report


# Stop if a recipient is missing from committees.csv.
def _assert_known_committees(df):
    known = {
        row['committee_short']
        for row in load_committees()
        if row.get('committee_short')
    }
    recipients = df['recipient_committee']
    unknown = recipients[recipients.notna() & ~recipients.isin(known)]
    if unknown.empty:
        return

    logger.error("  ABORT - %s rows have an unknown recipient:", f"{len(unknown):,}")
    for committee, count in unknown.value_counts().items():
        logger.error("    %-14s %s rows", committee, f"{count:,}")
    logger.error("  Add each recipient to data/reference/committees.csv.")
    raise SystemExit(1)


# Keep US ZIPs as five digits and foreign postcodes as filed.
def _ensure_zip_format(df):
    us = ~foreign_address_mask(df)
    zips = df['contributor_zip']

    if zips.dtype.name in ('object', 'string'):
        valid = zips.isna() | zips.astype(str).str.match(r'^\d{5}$', na=False)
        if (valid | ~us).all():
            return

    cleaned = (
        zips.astype('string')
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
        .str.replace(r'[^\d]', '', regex=True)
        .str.zfill(5)
        .str[:5]
    )
    invalid = cleaned.notna() & (
        ~cleaned.str.match(r'^\d{5}$', na=False) | cleaned.eq('00000')
    )
    cleaned[invalid] = pd.NA
    df['contributor_zip'] = cleaned.where(us, zips.astype('string'))


# Show row counts, donor counts and quality results.
def _print_summary(df, quality, output):
    individuals = df[df['entity_type'] == 'INDIVIDUAL']
    committees = int(df['entity_type'].eq('COMMITTEE/PAC').sum())
    organizations = int(df['entity_type'].eq('ORGANIZATION').sum())
    donors = individuals['donor_key'].nunique()

    logger.info("\n" + '=' * 55)
    logger.info("  Output: %s", output)
    logger.info("  Rows:   %s", f"{len(df):,}")
    logger.info("  Donors: %s individuals", f"{donors:,}")
    logger.info(
        "  Filings: %s individual, %s committee, %s organization",
        f"{len(individuals):,}",
        f"{committees:,}",
        f"{organizations:,}",
    )

    if quality['issues']:
        logger.info("  Quality issues: %s", quality['issues'])
    else:
        logger.info("  Quality gates: all applicable checks passed")
    not_run = [
        name for name, check in quality['checks'].items()
        if isinstance(check, dict) and check.get('not_run')
    ]
    if not_run:
        logger.info(
            "  Quality gates not run yet (need resolve.py --apply): %s",
            ", ".join(not_run),
        )

    if not individuals.empty:
        logger.info("\n  Top occupation categories:")
        top = individuals['occupation_category'].value_counts().head(10)
        for category, total in top.items():
            logger.info("    %-30s %7s", category, f"{total:,}")
    logger.info('=' * 55)


# Save the CSV only after the applicable checks pass.
def _save_if_gates_pass(output, out_dir, output_path) -> dict:
    # Resolve reruns the checks that need employer_status.
    quality = run_quality_gates(output)
    write_json_atomic(os.path.join(out_dir, 'quality_gates.json'), {**quality, 'stage': 'clean'}, indent=2)
    if not quality['passed']:
        logger.error("  ABORT - quality gates failed, %s kept as it was:", output_path)
        for issue in quality['issues']:
            logger.error("    %s", issue)
        raise SystemExit(1)
    check_output('clean', (), output.columns)
    write_csv_atomic(output, output_path, index=False)
    return quality


# Write the change audit, missing fields and quality scan.
def _write_reports(cleaned, original_rows, missing, trail, out_dir, output_path) -> None:
    audit = write_audit(cleaned, original_rows, out_dir, trail)
    logger.info(
        "  Audit: %s changes in %s cells, %s untracked",
        f"{audit['changes']:,}",
        f"{audit['changed_cells']:,}",
        f"{audit['untracked_changes']:,}",
    )

    people = cleaned.loc[cleaned['entity_type'].eq('INDIVIDUAL')].set_index('sub_id')
    missing = missing[missing['sub_id'].isin(people.index)].copy()
    missing['contributor_name'] = missing['sub_id'].map(people['contributor_name'])
    write_csv_atomic(missing, os.path.join(out_dir, 'missing_report.csv'), index=False)

    scan_report = _write_quality_scan(output_path, out_dir)
    logger.info(
        "  Quality scan: %s employer abbreviations, %s near-duplicate groups, "
        "%s name-drift rows, %s address-variant groups",
        f"{scan_report['employer_abbreviations']['distinct_employers_flagged']:,}",
        f"{scan_report['employer_near_duplicates']['groups']:,}",
        f"{scan_report['name_composite_drift']['rows']:,}",
        f"{scan_report['address_order_variants']['groups']:,}",
    )


if __name__ == '__main__':
    main()
