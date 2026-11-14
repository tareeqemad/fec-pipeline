"""Pull one committee's Schedule A contributions from the FEC API, so the raw file matches the FEC.

Run: python pull.py C00797670 --period 2026

Steps, in order (main -> pull_committee):
    1. read_existing_ids      the filing IDs already in data/raw/contributions.csv
    2. iter_pages             every page of the committee's period from the FEC API (api.py)
    3. _read_new_filings      the rows of the filings not saved yet (rows.py)
    4. append_source_fields   their FEC source fields, then the rows themselves, page by page
    5. drop_replaced_filings  after a complete scan, the saved filings the FEC no longer has: an amended report
                              replaced them (same transaction_id, new sub_id) or the committee withdrew them.
                              They are listed in replaced_filings.csv, and the rules and the donor key cache
                              move from the old sub_id to the new one.
"""

import argparse
import csv
import math
import os
import re
from datetime import date
from pathlib import Path

import requests
from tqdm import tqdm

from fec.common.committees import committee_id_to_name
from fec.common.log import get_logger
from fec.common.settings import (
    DONOR_KEYS_CSV,
    FEC_SOURCE_CSV,
    RAW_CSV,
    REPLACED_FILINGS_CSV,
    RULES_DIR,
    load_env,
)
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


def drop_replaced_filings(committee_id: str, period: int, current_ids: set[str]) -> dict[str, str]:
    """Remove this period's saved filings the FEC no longer returns; return old sub_id -> new sub_id ('' if none)."""
    raw_bytes = RAW_CSV.read_bytes()
    encoding = "utf-8-sig" if raw_bytes[:3] == b"\xef\xbb\xbf" else "utf-8"
    raw_text = raw_bytes.decode(encoding)
    newline = "\r\n" if "\r\n" in raw_text else "\n"
    lines = raw_text.split(newline)
    saved = list(csv.DictReader(lines))
    # One filing per line, so lines can be dropped without rewriting the others.
    if len(saved) != len([line for line in lines[1:] if line]):
        raise PullError(f"{RAW_CSV} has a filing written over several lines; it was not changed")
    in_period = [
        row
        for row in saved
        if row["committee_id"] == committee_id and row["two_year_transaction_period"] == str(period)
    ]
    current_by_transaction = {row["transaction_id"]: row["sub_id"] for row in in_period if row["sub_id"] in current_ids}
    replaced = {
        row["sub_id"]: current_by_transaction.get(row["transaction_id"], "")
        for row in in_period
        if row["sub_id"] not in current_ids
    }
    if not replaced:
        return {}

    kept = [lines[0]] + [line for row, line in zip(saved, lines[1:]) if row["sub_id"] not in replaced]
    RAW_CSV.write_text(newline.join(kept) + newline, encoding=encoding, newline="")
    _record_replaced(committee_id, period, replaced, in_period)
    _follow_new_ids({old: new for old, new in replaced.items() if new})
    withdrawn = sum(1 for new in replaced.values() if not new)
    logger.info("[REPLACED] %s filings the FEC re-issued, %s it no longer lists", len(replaced) - withdrawn, withdrawn)
    return replaced


def _record_replaced(committee_id: str, period: int, replaced: dict[str, str], in_period: list[dict]) -> None:
    transactions = {row["sub_id"]: row["transaction_id"] for row in in_period}
    is_new = not REPLACED_FILINGS_CSV.exists() or REPLACED_FILINGS_CSV.stat().st_size == 0
    with open(REPLACED_FILINGS_CSV, "a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(["old_sub_id", "new_sub_id", "committee_id", "period", "transaction_id", "replaced_on"])
        for old, new in replaced.items():
            writer.writerow([old, new, committee_id, period, transactions[old], date.today().isoformat()])


def _follow_new_ids(new_id_of: dict[str, str]) -> None:
    """Point the reviewed rules and the donor key cache at the filing that replaced the one they named."""
    if not new_id_of:
        return
    for path in [*sorted(RULES_DIR.glob("*.csv")), DONOR_KEYS_CSV]:
        if not path.exists():
            continue
        text = path.read_bytes().decode("utf-8")
        changed = re.sub(r"\b\d+\b", lambda number: new_id_of.get(number.group(), number.group()), text)
        if changed != text:
            path.write_bytes(changed.encode("utf-8"))


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
    current_ids, filings_in_period = set(), None

    logger.info("[START] %s / %s -> %s", committee_id, period, RAW_CSV)
    progress = tqdm(desc=f"{committee_id}/{period}", unit="pg")
    with open(RAW_CSV, "a", newline="", encoding="utf-8") as handle, progress:
        writer = csv.writer(handle)
        if handle.tell() == 0:
            writer.writerow(COLUMNS)
        for page in iter_pages(session, params, limiter):
            filings_in_period = (page.get("pagination") or {}).get("count") or filings_in_period
            if filings_in_period:
                progress.total = math.ceil(filings_in_period / PER_PAGE)
            progress.update(1)
            current_ids |= {str(result.get("sub_id") or "").strip() for result in page.get("results", [])} - {""}

            rows, source_rows = _read_new_filings(page.get("results", []), saved_ids, counts)
            # Save the source fields first, so a saved filing always has them.
            append_source_fields(FEC_SOURCE_CSV, source_rows)
            writer.writerows(rows)
            handle.flush()
            counts["new"] += len(rows)
            counts["pages"] += 1

    # Only a complete scan may remove saved filings; a cut scan would look like missing filings.
    if filings_in_period and len(current_ids) == filings_in_period:
        drop_replaced_filings(committee_id, period, current_ids)
    else:
        logger.warning("Scan saw %s of %s filings; no saved filing was removed", len(current_ids), filings_in_period)

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
