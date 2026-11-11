"""Pull new Schedule A contributions of one committee from the FEC API.

Run: python pull.py C00797670 --period 2026

Steps, in order (main -> pull_committee):
    1. read_existing_ids      the filing IDs already in data/raw/contributions.csv
    2. iter_pages             every page of the committee's period from the FEC API (api.py)
    3. _read_new_filings      the rows of the filings not saved yet (rows.py)
    4. append_source_fields   their FEC source fields, then the rows themselves, page by page
"""

import argparse
import csv
import math
import os
from datetime import date
from pathlib import Path

import requests
from tqdm import tqdm

from fec.common.committees import committee_id_to_name
from fec.common.log import get_logger
from fec.common.settings import FEC_SOURCE_CSV, RAW_CSV, load_env
from fec.pull.api import PullError, RateLimiter, iter_pages
from fec.pull.rows import COLUMNS, FIELDS, SOURCE_COLUMNS, build_row, build_source_row

logger = get_logger(__name__)

PER_PAGE = 100


def read_existing_ids(csv_path: Path, committee_id: str, period: int) -> set[str]:
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


def append_source_fields(path: Path, source_rows: list[dict]) -> None:
    """Append source fields, adding the CSV header when needed."""
    if not source_rows:
        return
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerows(source_rows)


def _read_new_filings(results: list[dict], saved_ids: set[str], counts: dict) -> tuple[list[list], list[dict]]:
    rows, source_rows = [], []
    for result in results:
        sub_id = str(result.get("sub_id") or "").strip()
        if not sub_id:
            counts["no_sub_id"] += 1
        elif sub_id in saved_ids:
            counts["skipped"] += 1
        else:
            saved_ids.add(sub_id)
            rows.append(build_row(sub_id, result))
            source_rows.append(build_source_row(sub_id, result))
    return rows, source_rows


def pull_committee(committee_id: str, period: int) -> None:
    """Scan the full period and append only new filing IDs."""
    api_key = os.getenv("FEC_API_KEY")
    if not api_key:
        raise PullError("missing env FEC_API_KEY - check .env file")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    saved_ids = read_existing_ids(RAW_CSV, committee_id, period)
    logger.info("[SCAN] period %s; skipping %s saved sub_id values", period, f"{len(saved_ids):,}")

    params = {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": PER_PAGE,
        "fields": ",".join(FIELDS),
    }
    session = requests.Session()
    session.headers.update({"User-Agent": "fec-pull/1.0", "Accept-Encoding": "gzip, deflate"})
    limiter = RateLimiter(int(os.getenv("FEC_RPM", "15")))
    counts = {"new": 0, "skipped": 0, "no_sub_id": 0, "pages": 0}

    logger.info("[START] %s / %s -> %s", committee_id, period, RAW_CSV)
    progress = tqdm(desc=f"{committee_id}/{period}", unit="pg")
    with open(RAW_CSV, "a", newline="", encoding="utf-8") as handle, progress:
        writer = csv.writer(handle)
        if handle.tell() == 0:
            writer.writerow(COLUMNS)
        for page in iter_pages(session, params, limiter):
            filings_in_period = (page.get("pagination") or {}).get("count")
            if filings_in_period:
                progress.total = math.ceil(filings_in_period / PER_PAGE)
            progress.update(1)

            rows, source_rows = _read_new_filings(page.get("results", []), saved_ids, counts)
            # Save the source fields first, so a saved filing always has them.
            append_source_fields(FEC_SOURCE_CSV, source_rows)
            writer.writerows(rows)
            handle.flush()
            counts["new"] += len(rows)
            counts["pages"] += 1

    summary = f"new={counts['new']:,} skipped={counts['skipped']:,} no_sub_id={counts['no_sub_id']:,}"
    logger.info("[DONE] %s / %s: %s pages=%s file=%s", committee_id, period, summary, counts["pages"], RAW_CSV)


def main(argv: list[str] | None = None) -> int:
    """Validate the committee and period, then pull new records."""
    parser = argparse.ArgumentParser(description="Check a committee's full FEC period and save new records.")
    parser.add_argument("committee_id", help="Committee ID from committees.csv.")
    period_help = "Two-year transaction period; an odd year means the period it ends in (default: the current one)."
    parser.add_argument("--period", type=int, default=date.today().year, help=period_help)
    args = parser.parse_args(argv)
    load_env()

    committee_id = args.committee_id.strip().upper()
    if committee_id not in committee_id_to_name():
        parser.error(f"{committee_id} is not configured in data/reference/committees.csv")
    period = args.period + args.period % 2

    logger.info("FEC pull - %s, period %s, full period, new records only", committee_id, period)
    try:
        pull_committee(committee_id, period)
    except PullError as error:
        logger.error("  %s", error)
        return 2
    except Exception as error:
        logger.error("  %s failed: %s: %s", committee_id, type(error).__name__, error)
        return 1
    logger.info("Done. Run clean.py next to process the new data.")
    return 0
