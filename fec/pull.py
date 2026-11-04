"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

from fec.env import RAW_CSV
from fec.fec_api import PullError, RateLimiter, build_session, fetch_page, required_env
from fec.pull_rows import COLUMNS, FIELDS, build_row

log = logging.getLogger(__name__)


# Find saved transaction IDs and amounts for this committee and period.
def read_saved(
    csv_path: Path,
    committee_id: str,
    period: int,
) -> dict[str, str]:
    if not csv_path.exists():
        return {}

    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        return {
            row["transaction_id"]: row["contribution_receipt_amount"]
            for row in csv.DictReader(handle)
            if row.get("committee_id") == committee_id
            and row.get("two_year_transaction_period") == str(period)
        }


# Follow FEC pagination cursors until no results or cursor remain.
def iter_pages(session, params: dict, limiter: RateLimiter) -> Iterator[dict]:
    params = dict(params)
    while True:
        data = fetch_page(session, params, limiter)
        yield data

        if not data.get("results"):
            return
        indexes = (data.get("pagination") or {}).get("last_indexes")
        if not indexes:
            return
        params["last_contribution_receipt_date"] = indexes.get(
            "last_contribution_receipt_date"
        )
        params["last_index"] = indexes.get("last_index")


# Request the configured fields for one committee and period.
def _pull_params(api_key: str, committee_id: str, period: int) -> dict:
    return {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(FIELDS),
    }


# Scan the full period and append only new transactions.
def run(committee_id: str, period: int) -> None:
    api_key = required_env("FEC_API_KEY")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    csv_path = RAW_CSV
    saved = read_saved(csv_path, committee_id, period)
    log.info("[SCAN] period %s; skipping %s saved transactions", period, f"{len(saved):,}")

    params = _pull_params(api_key, committee_id, period)
    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    stats = {"new_rows": 0, "duplicates": 0, "missing_ids": 0, "pages": 0}

    log.info("[START] %s / %s -> %s", committee_id, period, csv_path)
    progress = None
    try:
        try:
            with open(csv_path, "a", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                if handle.tell() == 0:
                    writer.writerow(COLUMNS)

                for data in iter_pages(session, params, limiter):
                    if progress is None:
                        total = (data.get("pagination") or {}).get("count")
                        if total:
                            progress = tqdm(
                                total=-(-total // 100),
                                desc=f"{committee_id}/{period}", unit="pg",
                            )
                    stats["pages"] += 1
                    if progress:
                        progress.update(1)

                    # Validate this page before saving any of its rows.
                    page = []
                    for result in data.get("results", []):
                        sub_id = str(result.get("sub_id") or "").strip()
                        if not sub_id:
                            stats["missing_ids"] += 1
                            continue
                        transaction_id = str(result.get("transaction_id") or "").strip()
                        if not transaction_id:
                            raise PullError(f"FEC returned {sub_id} without a transaction_id")
                        amount = result.get("contribution_receipt_amount")
                        if transaction_id in saved:
                            # An amended amount needs a person; never change it silently.
                            if float(saved[transaction_id]) != float(amount or 0):
                                log.warning("Amount changed for %s: %s -> %s", transaction_id, saved[transaction_id], amount)
                            stats["duplicates"] += 1
                            continue
                        page.append(build_row(result))
                        saved[transaction_id] = str(amount)
                    writer.writerows(page)
                    handle.flush()
                    stats["new_rows"] += len(page)
        finally:
            if progress:
                progress.close()
    except KeyboardInterrupt:
        _log_summary(
            "STOPPED", committee_id, period, stats, csv_path, log.warning,
        )
        raise

    _log_summary("DONE", committee_id, period, stats, csv_path, log.info)


# Log the saved, skipped and missing-ID counts.
def _log_summary(status, committee_id, period, stats, csv_path, writer) -> None:
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
