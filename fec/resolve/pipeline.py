"""Resolve employer history and addresses, then write the cleaned CSV."""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd

from fec.cleaning.reports.gates import run_quality_gates
from fec.cleaning.work.status import classify_employer_statuses
from fec.config.cities import expand_city_abbreviations
from fec.contract import check_input, check_output
from fec.env import (
    CACHE_DIR, CLEANED_CSV, MANUAL_EMPLOYER_ADDRESSES_CSV,
    MANUAL_EMPLOYER_OVERRIDES_CSV, RAW_CSV, REPORTS_DIR, load_env,
)
from fec.io import read_pipeline_csv, write_csv_atomic, write_json_atomic
from fec.log import get_logger
from fec.resolve.apply import apply_results
from fec.resolve.cache import Cache, EMPLOYER_ADDR_CACHE, PREV_EMPLOYER_CACHE, dedup_by_resolved_address
from fec.resolve.employers import step_ai_lookup
from fec.resolve.fec import drop_unproven_source_entries, step_fec_api
from fec.resolve.manual import load_manual_locations, load_manual_previous_employers
from fec.resolve.previous import step_cross_record
from fec.resolve.search import PROVIDER, AIQuotaExhausted, get_ai_model


TIERS = [
    (1, "500K+",     500_000, float("inf")),
    (2, "100K-500K", 100_000, 500_000),
    (3, "50K-100K",   50_000, 100_000),
    (4, "10K-50K",    10_000,  50_000),
    (5, "1K-10K",      1_000,  10_000),
    (6, "<1K",             0,   1_000),
]


logger = get_logger(__name__)


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Resolve employer addresses"
    )
    parser.add_argument("--apply", action="store_true", help="Write resolve columns to CSV")
    args = parser.parse_args()
    if not args.apply:
        parser.error("use --apply to write resolved data")
    return args


def _load_data(csv_path: str):
    previous = Cache(os.path.join(CACHE_DIR, PREV_EMPLOYER_CACHE))
    employers = Cache(os.path.join(CACHE_DIR, EMPLOYER_ADDR_CACHE))

    df = read_pipeline_csv(csv_path)
    amounts = pd.to_numeric(df["contribution_receipt_amount"], errors="coerce")
    unreadable = amounts.isna()
    if unreadable.any():
        # a missing amount is fixed in cleaning; writing it back as $0 would hide it
        examples = ", ".join(df.loc[unreadable, "sub_id"].astype(str).head(5))
        raise ValueError(
            f"{int(unreadable.sum()):,} row(s) with a missing or unreadable "
            f"contribution_receipt_amount (sub_id {examples})"
        )
    df["contribution_receipt_amount"] = amounts
    totals = _compute_donor_totals(df)
    return df, totals, previous, employers


def _deduplicate_address_cache(addr_cache) -> None:
    logger.info("\n-- Step 3b: Address-cache aliases --")
    aliases, groups = dedup_by_resolved_address(addr_cache)
    logger.info(f"    created {aliases:,} aliases across {groups:,} groups")


def _run_steps(
    df,
    donor_totals,
    prev_cache,
    addr_cache,
) -> bool:
    """Run the resolve stages; return whether AI stopped early."""
    logger.info("\n-- Step 0: Manual overrides --")
    load_manual_locations(MANUAL_EMPLOYER_ADDRESSES_CSV, addr_cache)
    load_manual_previous_employers(MANUAL_EMPLOYER_OVERRIDES_CSV, df, prev_cache)
    unverified = drop_unproven_source_entries(prev_cache, df)
    if unverified:
        prev_cache.save()
        logger.info(f"    FEC history: withheld {unverified:,} answers without matching source evidence")

    logger.info("\n-- Step 1: Cross-record previous employers --")
    step_cross_record(df, prev_cache)

    logger.info("\n-- Step 2: FEC previous employers --")
    step_fec_api(df, prev_cache, donor_totals)

    logger.info(f"\n-- Step 3: Employer locations ({PROVIDER} {get_ai_model()}) --")
    ai_incomplete = False
    try:
        step_ai_lookup(
            df,
            prev_cache,
            addr_cache,
            donor_totals,
        )
    except AIQuotaExhausted as error:
        ai_incomplete = True
        logger.error(f"\n  STOPPED: {error}")
        logger.warning(
            "  Continuing with cached addresses; unresolved employers stay blank."
        )

    _deduplicate_address_cache(addr_cache)
    return ai_incomplete


QUALITY_GATES_JSON = "quality_gates.json"


def _write_quality_gates(quality: dict, csv_written: bool) -> None:
    """Write the quality report for the resolved data."""
    report = {**quality, "stage": "resolve", "csv_written": csv_written}
    write_json_atomic(Path(REPORTS_DIR) / QUALITY_GATES_JSON, report, indent=2)


def _filed_employers() -> dict:
    """sub_id -> the raw employer text; empty when the raw file is absent."""
    if not RAW_CSV.exists():
        return {}
    raw = pd.read_csv(RAW_CSV, dtype=str, keep_default_na=False, usecols=["sub_id", "contributor_employer"])
    return dict(zip(raw["sub_id"].str.strip(), raw["contributor_employer"]))


def _write_results(
    df: pd.DataFrame,
    csv_path: str,
    prev_cache,
    addr_cache,
    input_columns,
) -> pd.DataFrame:
    logger.info(f"\n-- Writing results -> {csv_path} --")
    df = apply_results(df, prev_cache, addr_cache, _filed_employers())
    quality = run_quality_gates(df)
    if not quality["passed"]:
        _write_quality_gates(quality, csv_written=False)
        details = "; ".join(quality["issues"]) or "quality gate failed"
        raise ValueError(f"Resolved CSV was not written: {details}")

    # Expand Saint/Mount/Fort in resolved employer cities, matching clean.py.
    df["employer_city"] = df["employer_city"].map(expand_city_abbreviations)

    check_output("resolve", input_columns, df.columns)
    write_csv_atomic(df, csv_path, index=False)
    # the gates read no employer_city, so the report above describes this file
    _write_quality_gates(quality, csv_written=True)
    logger.info(f"  Written {len(df):,} rows")
    return df


def main() -> None:
    _parse_args()
    load_env()

    csv_path = str(CLEANED_CSV)
    if not CLEANED_CSV.exists():
        logger.info("Error: no cleaned CSV found")
        sys.exit(1)

    data = _load_data(csv_path)
    df, donor_totals, prev_cache, addr_cache = data
    check_input("resolve", df.columns)
    input_columns = list(df.columns)

    logger.info(f"\n{'=' * 60}")
    logger.info("  FEC Resolve - Employer Addresses")
    logger.info(f"  File:  {csv_path}")
    logger.info(f"{'=' * 60}")
    total_start = time.time()

    ai_incomplete = _run_steps(
        df,
        donor_totals,
        prev_cache,
        addr_cache,
    )

    df = _write_results(df, csv_path, prev_cache, addr_cache, input_columns)

    minutes, seconds = divmod(int(time.time() - total_start), 60)
    logger.info(f"\n  Time: {minutes}m {seconds}s")
    show_stats(df, prev_cache, addr_cache, donor_totals)

    if ai_incomplete:
        logger.warning(
            "\n  PARTIAL: cached addresses were written, but AI resolution is incomplete."
        )
        logger.warning(
            "  After adding credits, run clean.py, then geocode.py, then "
            "resolve.py --apply. Cached lookups are kept; the resolved CSV "
            "cannot be used as resolve input again."
        )
        sys.exit(2)


def _compute_donor_totals(df: pd.DataFrame) -> pd.Series:
    """Compute total per donor_key and assign tier number."""
    totals = df.groupby("donor_key")["contribution_receipt_amount"].sum().reset_index()
    totals.columns = ["donor_key", "donor_total"]

    # map a donation amount to its tier number
    def _tier(amount):
        for tier_number, _, low, high in TIERS:
            if low <= amount < high:
                return tier_number
        return 6

    totals["tier"] = totals["donor_total"].apply(_tier)
    return totals


def show_stats(
    df: pd.DataFrame,
    prev_cache,
    addr_cache,
    donor_totals: pd.Series,
) -> None:
    """Show resolution status."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    latest = (individuals.sort_values("contribution_receipt_date")
              .drop_duplicates("donor_key", keep="last"))
    latest = latest.merge(
        donor_totals[["donor_key", "donor_total", "tier"]],
        on="donor_key", how="left",
    )
    if "employer_status" not in latest:
        latest["employer_status"] = classify_employer_statuses(latest)
    if "employer_address" not in latest:
        latest["employer_address"] = ""

    logger.info("\n  -- Cache Sizes --")
    logger.info(f"    Previous employer:  {len(prev_cache):>7,}")
    logger.info(f"    Employer addresses: {len(addr_cache):>7,}")

    logger.info("\n  -- Resolution by Tier --")
    logger.info(
        f"  {'Tier':15s} {'Donors':>7s} {'HasEmp':>7s} {'Retired':>7s} "
        f"{'SelfEmp':>7s} {'Skip':>7s} {'AddrOK':>7s}"
    )
    logger.info(
        f"  {'-' * 15} {'-' * 7} {'-' * 7} {'-' * 7} "
        f"{'-' * 7} {'-' * 7} {'-' * 7}"
    )

    for tier_number, label, _, _ in TIERS:
        tier = latest[latest["tier"] == tier_number]
        n_donors = len(tier)
        if n_donors == 0:
            continue

        status = tier["employer_status"]
        has_emp = status.eq("active").sum()
        retired = status.eq("retired").sum()
        self_emp = status.eq("self_employed").sum()
        skip = n_donors - has_emp - retired - self_emp
        addr_ok = tier["employer_address"].fillna("").str.strip().ne("").sum()

        logger.info(
            f"  {label:15s} {n_donors:>7,} {has_emp:>7,} "
            f"{retired:>7,} {self_emp:>7,} {skip:>7,} {addr_ok:>7,}"
        )
