"""Clean the configured FEC contributions file."""
import json
import os

import pandas as pd

from fec.cleaning.audit import write_audit
from fec.cleaning.pipeline import clean_pipeline
from fec.cleaning.quality import run_quality_gates, save_report
from fec.env import CLEANED_CSV, RAW_CSV
from fec.io import read_pipeline_csv
from fec.log import get_logger

logger = get_logger(__name__)


def _drop_internal_cols(df):
    """Remove working columns."""
    from fec.config.data import INTERNAL_OUTPUT_COLUMNS

    internal = [column for column in df.columns if column.startswith('_')]
    internal += [column for column in INTERNAL_OUTPUT_COLUMNS if column in df.columns]
    return df.drop(columns=internal)


def _write_quality_scan(output_path, out_dir):
    """Write the quality scan."""
    from fec.cleaning.quality_scan import scan

    df = pd.read_csv(
        output_path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
    )
    report = scan(df)
    path = os.path.join(out_dir, 'quality_scan.json')
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
    return report


def _assert_known_committees(df):
    """Reject unknown recipients."""
    from fec.committees import load_committees

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
    logger.error("  Add each recipient to data/database/committees.csv.")
    raise SystemExit(1)


def _ensure_zip_format(df):
    """Keep US ZIPs as five digits; a foreign postcode (SW1A 2AA) stays as filed."""
    from fec.cleaning.foreign_addresses import foreign_address_mask

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


def main():
    from fec import env

    env.load_env()

    input_path = str(RAW_CSV)
    output_path = str(CLEANED_CSV)
    out_dir = os.path.dirname(output_path) or '.'
    os.makedirs(out_dir, exist_ok=True)

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
    )
    output = _drop_internal_cols(cleaned).copy()

    _assert_known_committees(output)
    _ensure_zip_format(output)

    # the gates check the output before it replaces the last good file
    # (the gates that need employer_status read not_run here; resolve.py
    # --apply reruns every gate and overwrites quality_gates.json)
    quality = run_quality_gates(output)
    with open(os.path.join(out_dir, 'quality_gates.json'), 'w') as handle:
        json.dump({**quality, 'stage': 'clean'}, handle, indent=2)
    if not quality['passed']:
        logger.error("  ABORT - quality gates failed, %s kept as it was:", output_path)
        for issue in quality['issues']:
            logger.error("    %s", issue)
        raise SystemExit(1)
    output.to_csv(output_path, index=False)

    audit = write_audit(cleaned, original_rows, out_dir, trail)
    logger.info(
        "  Audit: %s changes in %s cells, %s untracked",
        f"{audit['changes']:,}",
        f"{audit['changed_cells']:,}",
        f"{audit['untracked_changes']:,}",
    )

    save_report(missing, out_dir, 'missing_report')

    scan = _write_quality_scan(output_path, out_dir)
    logger.info(
        "  Quality scan: %s employer abbreviations, %s near-duplicate groups, "
        "%s name-drift rows, %s address-variant groups",
        f"{scan['employer_abbreviations']['distinct_employers_flagged']:,}",
        f"{scan['employer_near_duplicates']['groups']:,}",
        f"{scan['name_composite_drift']['rows']:,}",
        f"{scan['address_order_variants']['groups']:,}",
    )

    _print_summary(output, quality, output_path)
