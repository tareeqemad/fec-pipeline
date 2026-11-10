#!/usr/bin/env python3
"""Read, clean, check and save FEC contributions."""

import os
import time

import pandas as pd

from fec import env
from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.cleaning.pipeline import clean_pipeline
from fec.cleaning.reports.gates import run_quality_gates
from fec.committees import load_committees
from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES
from fec.config.data import INTERNAL_OUTPUT_COLUMNS
from fec.contract import CLEAN_COLUMNS, check_output
from fec.env import CLEANED_CSV, DONOR_KEYS_CSV, RAW_CSV, REPORTS_DIR
from fec.io import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger

logger = get_logger(__name__)


def main() -> pd.DataFrame:
    start = time.perf_counter()
    env.load_env()

    input_path = str(RAW_CSV)
    output_path = str(CLEANED_CSV)
    out_dir = str(REPORTS_DIR)
    for folder in (out_dir, CLEANED_CSV.parent):
        os.makedirs(folder, exist_ok=True)

    df = read_pipeline_csv(input_path)
    logger.info("Reading: %s (%s rows)", os.path.relpath(input_path, env.PROJECT_ROOT), f"{len(df):,}")

    cleaned = clean_pipeline(df, out_dir=out_dir, earlier_keys=_earlier_donor_keys())
    output = cleaned.drop(columns=[
        column for column in cleaned.columns
        if column.startswith('_') or column in INTERNAL_OUTPUT_COLUMNS
    ])

    _assert_known_committees(output)
    _ensure_zip_format(output)

    _save_if_gates_pass(output, out_dir, output_path)
    _save_donor_keys(cleaned)
    _write_new_occupations(output, out_dir)
    _print_summary(output, output_path, out_dir, time.perf_counter() - start)
    logger.info("Next: python geocode.py")
    return output


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


# US ZIPs as five digits; foreign postcodes as filed.
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


# Each filing's donor key from the last run, or {} the first time.
def _earlier_donor_keys() -> dict:
    if not DONOR_KEYS_CSV.exists():
        return {}
    keys = pd.read_csv(DONOR_KEYS_CSV, dtype=str, keep_default_na=False)
    return dict(zip(keys['sub_id'], keys['donor_key']))


# Remember each filing's donor key for the next run.
def _save_donor_keys(cleaned) -> None:
    keys = cleaned[['sub_id', '_matched_key']].rename(columns={'_matched_key': 'donor_key'})
    write_csv_atomic(keys, str(DONOR_KEYS_CSV), index=False)


# Occupations missing from the category table, for optional review.
def _write_new_occupations(df, out_dir) -> None:
    occupation = df['contributor_occupation'].fillna('')
    new = df[df['entity_type'].eq('INDIVIDUAL') & occupation.ne('') & ~occupation.isin(CATEGORY_OVERRIDES)]
    report = new.groupby(['contributor_occupation', 'occupation_category']).size().reset_index(name='rows')
    write_csv_atomic(report, os.path.join(out_dir, 'new_occupations.csv'), index=False)
    if len(report):
        logger.info("New occupations: %s (data/reports/new_occupations.csv)", f"{len(report):,}")


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
    write_csv_atomic(output[list(CLEAN_COLUMNS)], output_path, index=False)


if __name__ == '__main__':
    main()
