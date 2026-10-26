#!/usr/bin/env python3
"""Read, clean, check and save FEC contributions."""

import os
import time

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
from fec.env import CLEANED_CSV, RAW_CSV, REPORTS_DIR
from fec.io import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger

logger = get_logger(__name__)


def main(expected: pd.DataFrame | None = None) -> pd.DataFrame:
    start = time.perf_counter()
    env.load_env()

    input_path = str(RAW_CSV)
    output_path = str(CLEANED_CSV)
    out_dir = str(REPORTS_DIR)
    for folder in (out_dir, CLEANED_CSV.parent):
        os.makedirs(folder, exist_ok=True)

    df = read_pipeline_csv(input_path)
    logger.info("Reading: %s (%s rows)", os.path.relpath(input_path, env.PROJECT_ROOT), f"{len(df):,}")

    original_rows = df[['sub_id']].copy()
    original_rows['row_index'] = original_rows.index.astype(int)

    cleaned, missing, trail = clean_pipeline(df, out_dir=out_dir)
    output = cleaned.drop(columns=[
        column for column in cleaned.columns
        if column.startswith('_') or column in INTERNAL_OUTPUT_COLUMNS
    ])

    _assert_known_committees(output)
    _ensure_zip_format(output)
    if expected is not None:
        _check_reclean(expected, output)

    _save_if_gates_pass(output, out_dir, output_path)
    _write_reports(cleaned, original_rows, missing, trail, out_dir, output_path)
    _print_summary(output, output_path, out_dir, time.perf_counter() - start)
    if expected is None:
        logger.info("Next: python geocode.py")
    return output


# City corrections may rename keys, but must keep contributions and donor groups.
def _check_reclean(expected: pd.DataFrame, output: pd.DataFrame) -> None:
    columns = ['sub_id', 'contribution_receipt_amount', 'donor_key', 'contributor_city']
    before = expected[columns].set_index('sub_id', verify_integrity=True).sort_index()
    after = output[columns].set_index('sub_id', verify_integrity=True).sort_index()
    if not before.index.equals(after.index):
        raise ValueError('City correction changed contribution IDs; the previous CSV was kept')
    amount = 'contribution_receipt_amount'
    if not pd.to_numeric(before[amount]).eq(pd.to_numeric(after[amount])).all():
        raise ValueError('City correction changed amounts; the previous CSV was kept')
    groups = pd.DataFrame({'before': before['donor_key'], 'after': after['donor_key']})
    split = groups.groupby('before', dropna=False)['after'].nunique(dropna=False).gt(1).any()
    merged = groups.groupby('after', dropna=False)['before'].nunique(dropna=False).gt(1).any()
    if split or merged:
        raise ValueError('City correction changed donor groups; the previous CSV was kept')
    if not before['contributor_city'].fillna('').eq(after['contributor_city'].fillna('')).all():
        raise ValueError('City corrections were not applied as expected; the previous CSV was kept')


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


# Show counts, output paths and elapsed time.
def _print_summary(df, output, out_dir, elapsed):
    donors = df.loc[df['entity_type'].eq('INDIVIDUAL'), 'donor_key'].nunique()
    minutes, seconds = divmod(round(elapsed), 60)
    logger.info("Clean checks passed.")
    logger.info("Rows: %s | Individual donors: %s", f"{len(df):,}", f"{donors:,}")
    logger.info("Saved: %s", os.path.relpath(output, env.PROJECT_ROOT))
    logger.info("Reports: %s", os.path.relpath(out_dir, env.PROJECT_ROOT))
    logger.info("Completed in %dm %02ds.", minutes, seconds)


# Save the CSV only after the applicable checks pass.
def _save_if_gates_pass(output, out_dir, output_path) -> None:
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


# Write the change audit, missing fields and quality scan.
def _write_reports(cleaned, original_rows, missing, trail, out_dir, output_path) -> None:
    write_audit(cleaned, original_rows, out_dir, trail)

    people = cleaned.loc[
        cleaned['entity_type'].eq('INDIVIDUAL'), ['sub_id', 'contributor_name']
    ].set_index('sub_id')
    missing = missing[missing['sub_id'].isin(people.index)].copy()
    missing['contributor_name'] = missing['sub_id'].map(people['contributor_name'])
    write_csv_atomic(missing, os.path.join(out_dir, 'missing_report.csv'), index=False)

    _write_quality_scan(output_path, out_dir)


if __name__ == '__main__':
    main()
