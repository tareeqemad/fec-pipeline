"""Pull new Schedule A contributions of one committee from the FEC API.

Run: python pull.py C00797670 --period 2026

Steps, in order (main -> pull_committee):
    1. read_existing_ids     the filing IDs already in data/raw/contributions.csv
    2. iter_pages            every page of the committee's period from the FEC API
    3. _write_pages          append only the new filings, and their FEC source fields
    4. _log_summary          how many filings were new
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from typing import TextIO

import requests
from tqdm import tqdm

from fec.common.committees import committee_id_to_name
from fec.common.log import get_logger
from fec.common.settings import FEC_SOURCE_CSV, RAW_CSV
from fec.pull.api import PullError, RateLimiter, build_session, fetch_page, required_env
from fec.pull.rows import COLUMNS, FIELDS, SOURCE_COLUMNS, build_row, source_values

log = logging.getLogger(__name__)


def source_path(csv_path: Path) -> Path:
    """Keep source fields beside the raw CSV."""
    return csv_path.parent / FEC_SOURCE_CSV.name


def append_source_fields(path: Path, records: list[dict]) -> None:
    """Append source fields, adding the CSV header when needed."""
    if not records:
        return
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS, restval="")
        if new:
            writer.writeheader()
        writer.writerows(records)


def _build_source_record(sub_id: str, result: dict) -> dict:
    """Keep source fields for a newly pulled filing."""
    return {"sub_id": sub_id, **source_values(result), "matched_by": "pulled", "fec_sub_id": ""}


def read_existing_ids(
    csv_path: Path,
    committee_id: str,
    period: int,
) -> set[str]:
    """Find saved filing IDs for this committee and period."""
    if not csv_path.exists():
        return set()

    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        return {
            row["sub_id"]
            for row in csv.DictReader(handle)
            if row.get("committee_id") == committee_id
            and row.get("two_year_transaction_period") == str(period)
            and row.get("sub_id")
        }


def iter_pages(session: requests.Session, params: dict, limiter: RateLimiter) -> Iterator[dict]:
    """Follow FEC pagination cursors until no results or cursor remain."""
    params = dict(params)
    while True:
        data = fetch_page(session, params, limiter)
        yield data

        if not data.get("results"):
            return
        indexes = (data.get("pagination") or {}).get("last_indexes")
        if not indexes:
            return
        params["last_contribution_receipt_date"] = indexes.get("last_contribution_receipt_date")
        params["last_index"] = indexes.get("last_index")


def _build_pull_params(api_key: str, committee_id: str, period: int) -> dict:
    """Request the configured fields for one committee and period."""
    return {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(FIELDS),
    }


def pull_committee(committee_id: str, period: int) -> None:
    """Scan the full period and append only new filing IDs."""
    api_key = required_env("FEC_API_KEY")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    csv_path = RAW_CSV
    existing_ids = read_existing_ids(csv_path, committee_id, period)
    log.info("[SCAN] period %s; skipping %s saved sub_id values", period, f"{len(existing_ids):,}")

    params = _build_pull_params(api_key, committee_id, period)
    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    stats = {"new_rows": 0, "duplicates": 0, "missing_ids": 0, "pages": 0}

    log.info("[START] %s / %s -> %s", committee_id, period, csv_path)
    pages = iter_pages(session, params, limiter)
    label = f"{committee_id}/{period}"
    try:
        with open(csv_path, "a", newline="", encoding="utf-8") as handle:
            _write_pages(handle, pages, label, source_path(csv_path), existing_ids, stats)
    except KeyboardInterrupt:
        _log_summary("STOPPED", committee_id, period, stats, csv_path, log.warning)
        raise

    _log_summary("DONE", committee_id, period, stats, csv_path, log.info)


def _write_pages(
    handle: TextIO,
    pages: Iterator[dict],
    label: str,
    sources_csv: Path,
    existing_ids: set[str],
    stats: dict[str, int],
) -> None:
    """Append each page's new rows, showing progress once the page count is known."""
    writer = csv.writer(handle)
    if handle.tell() == 0:
        writer.writerow(COLUMNS)

    progress = None
    try:
        for data in pages:
            if progress is None:
                progress = _start_progress(data, label)
            stats["pages"] += 1
            if progress:
                progress.update(1)

            rows, page_ids, sources = _read_new_rows(data, existing_ids, stats)
            # Save metadata first, so filings always have source fields.
            append_source_fields(sources_csv, sources)
            writer.writerows(rows)
            handle.flush()
            existing_ids.update(page_ids)
            stats["new_rows"] += len(rows)
    finally:
        if progress:
            progress.close()


def _start_progress(data: dict, label: str) -> tqdm | None:
    """Start a progress bar when the page reports the total row count."""
    total = (data.get("pagination") or {}).get("count")
    if not total:
        return None
    return tqdm(total=-(-total // 100), desc=label, unit="pg")


def _read_new_rows(
    data: dict, existing_ids: set[str], stats: dict[str, int]
) -> tuple[list[list], set[str], list[dict]]:
    """Validate one page and return its new rows, their IDs and their source fields."""
    rows = []
    page_ids = set()
    sources = []
    for result in data.get("results", []):
        sub_id = str(result.get("sub_id") or "").strip()
        if not sub_id:
            stats["missing_ids"] += 1
            continue
        if sub_id in existing_ids or sub_id in page_ids:
            stats["duplicates"] += 1
            continue
        rows.append(build_row(result))
        page_ids.add(sub_id)
        sources.append(_build_source_record(sub_id, result))
    return rows, page_ids, sources


def _log_summary(
    status: str,
    committee_id: str,
    period: int,
    stats: dict[str, int],
    csv_path: Path,
    writer: Callable[..., None],
) -> None:
    """Log the saved, skipped and missing-ID counts."""
    writer(
        "[%s] %s / %s: new=%s skipped=%s no_sub_id=%s pages=%s file=%s",
        status,
        committee_id,
        period,
        f"{stats['new_rows']:,}",
        f"{stats['duplicates']:,}",
        f"{stats['missing_ids']:,}",
        stats["pages"],
        csv_path,
    )


logger = get_logger(__name__)


def _round_up_to_period(year: int) -> int:
    """Use the even year ending the two-year FEC period."""
    return year + (year % 2)


CURRENT_PERIOD = _round_up_to_period(date.today().year)


def main(argv: list[str] | None = None) -> int:
    """Validate the committee and period, then pull new records."""
    parser = argparse.ArgumentParser(description="Check a committee's full FEC period and save new records.")
    parser.add_argument("committee_id", help="Committee ID from committees.csv.")
    parser.add_argument(
        "--period",
        type=int,
        default=CURRENT_PERIOD,
        help="Two-year transaction period (even year, default %(default)s).",
    )
    parser.add_argument("--full", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    from fec.common import settings as env

    env.load_env()

    committee_id = args.committee_id.strip().upper()
    if not re.fullmatch(r"C\d{8}", committee_id):
        parser.error("invalid committee id (expected C + 8 digits)")
    if committee_id not in committee_id_to_name():
        parser.error(f"{committee_id} is not configured in data/reference/committees.csv")

    period = _round_up_to_period(args.period)
    logger.info("=" * 60)
    logger.info(
        "  FEC pull - %s, period %s, full period, new records only",
        committee_id,
        period,
    )
    logger.info("=" * 60)

    try:
        pull_committee(committee_id=committee_id, period=period)
    except PullError as error:
        logger.error("  %s", error)
        return 2
    except Exception as error:
        logger.error("  %s failed: %s: %s", committee_id, type(error).__name__, error)
        return 1

    logger.info("\n" + "=" * 60)
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0
