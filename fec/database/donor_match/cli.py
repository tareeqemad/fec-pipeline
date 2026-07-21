"""CLI entry point for donor matching."""

import argparse
import os
import sys
from pathlib import Path

import pandas as pd

from fec.log import get_logger
from fec.env import CLEANED_CSV

from .constants import MERGE_THRESHOLD
from .matcher import match_donors
from .output import apply_donor_key, export_audit, show_examples, show_stats

logger = get_logger(__name__)

CSV_PATH = CLEANED_CSV


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score-based donor deduplication / entity resolution"
    )
    parser.add_argument("input", nargs="?", default=str(CSV_PATH))
    parser.add_argument("--apply", action="store_true", help="Write donor_key to CSV")
    parser.add_argument("--stats", action="store_true", help="Show detailed stats")
    parser.add_argument("--audit", action="store_true", help="Export merge audit CSV")
    parser.add_argument("--threshold", type=int, default=MERGE_THRESHOLD,
                        help=f"Merge threshold (default: {MERGE_THRESHOLD})")
    parser.add_argument("--examples", type=int, default=10)
    args = parser.parse_args()

    csv_path = args.input
    if not os.path.exists(csv_path):
        logger.error(f" {csv_path} not found")
        sys.exit(1)

    logger.info(f"{'='*60}")
    logger.info(f"  Donor Matching \u2014 Score-Based Entity Resolution")
    logger.info(f"{'='*60}")
    logger.info(f"  File:      {csv_path}")
    logger.info(f"  Threshold: {args.threshold}")

    df = pd.read_csv(csv_path, dtype=str, low_memory=False, keep_default_na=False,
                     na_values=['', '#N/A', '#NA', 'N/A', '#N/A N/A', 'NaN', 'nan', 'None'])
    logger.info(f"  Rows:      {len(df):,}")

    rid_to_key, audit_log = match_donors(df, threshold=args.threshold, verbose=True)

    if args.examples > 0:
        show_examples(df, rid_to_key, args.examples)

    if args.audit:
        export_audit(audit_log, Path(csv_path).parent / "donor_merge_audit.csv")

    if args.apply:
        logger.info(f"\n  Applying donor_key...")
        df = apply_donor_key(df, rid_to_key)

        from fec.database.post_merge_fixes import apply_post_merge_fixes
        n_post = apply_post_merge_fixes(df)
        if n_post:
            logger.info(f"  Post-merge fixes: {n_post:,} records corrected")

        logger.info(f"  Writing \u2192 {csv_path}")
        df.to_csv(csv_path, index=False)
        total_keys = df["donor_key"].nunique()
        indiv_keys = df[df["entity_type"] == "INDIVIDUAL"]["donor_key"].nunique()
        logger.info(f"\n  \u2713 donor_key applied")
        logger.info(f"  Unique donor_keys:  {total_keys:,} (individuals: {indiv_keys:,})")

    if args.stats:
        show_stats(df, rid_to_key)

    logger.info(f"\n{'='*60}")
