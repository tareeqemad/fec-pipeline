"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

from tqdm import tqdm

from fec.env import RAW_CSV
from fec.fec_api import RateLimiter, build_session, fetch_page, required_env
from fec.pull_rows import COLUMNS, FIELDS, build_row

log = logging.getLogger(__name__)


# load existing sub_ids and latest date for a committee period
def read_committee_state(
    csv_path: Path,
    committee_id: str,
    period: int,
) -> tuple[set[str], str | None]:
    """Return existing IDs and latest date for one committee period."""
    sub_ids: set[str] = set()
    latest_date = None
    if not csv_path.exists():
        return sub_ids, latest_date

    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if (
                row.get("committee_id") != committee_id
                or row.get("two_year_transaction_period") != str(period)
            ):
                continue
            if row.get("sub_id"):
                sub_ids.add(row["sub_id"])
            receipt_date = row.get("contribution_receipt_date")
            if receipt_date and (latest_date is None or receipt_date > latest_date):
                latest_date = receipt_date
    return sub_ids, latest_date


# yield successive cursor-paginated FEC API responses
def iter_pages(session, params: dict, limiter: RateLimiter) -> Iterator[dict]:
    """Yield cursor-paginated FEC responses."""
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


# log whether this pull is full, refresh, or fresh start
def _log_pull_mode(
    full: bool,
    period: int,
    latest_date: str | None,
    existing_count: int,
) -> None:
    if full:
        log.info("[FULL] rechecking period %s; deduping by sub_id", period)
    elif latest_date:
        log.info(
            "[REFRESH] %s existing rows; starting at %s",
            f"{existing_count:,}",
            latest_date,
        )
    else:
        log.info("[REFRESH] no existing data; pulling everything")


# build the FEC API request parameters for one pull
def _pull_params(
    api_key: str,
    committee_id: str,
    period: int,
    latest_date: str | None,
    full: bool,
) -> dict:
    params: dict[str, Any] = {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(FIELDS),
    }
    if latest_date and not full:
        params["min_date"] = latest_date
    return params


# fetch and write all pages of one pull to CSV
def _pull_pages(
    csv_path: Path,
    session,
    params: dict,
    limiter: RateLimiter,
    existing_ids: set[str],
    committee_id: str,
    period: int,
    stats: dict[str, int],
) -> None:
    progress = None
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
                            desc=f"{committee_id}/{period}",
                            unit="pg",
                        )

                stats["pages"] += 1
                if progress:
                    progress.update(1)

                for result in data.get("results", []):
                    row = build_row(result)
                    if row is None:
                        stats["missing_ids"] += 1
                    elif str(row[0]) in existing_ids:
                        stats["duplicates"] += 1
                    else:
                        writer.writerow(row)
                        existing_ids.add(str(row[0]))
                        stats["new_rows"] += 1
                handle.flush()
    finally:
        if progress:
            progress.close()


# path to the pull-state JSON file next to the CSV
def _pull_state_path(csv_path: Path) -> Path:
    return csv_path.parent / "pull_state.json"


# load the pull-state JSON, or empty dict if none exists
def _read_pull_state(csv_path: Path) -> dict:
    path = _pull_state_path(csv_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# persist a committee period's pull status to the state file
def _mark_pull(csv_path: Path, committee_id: str, period: int, status: str) -> None:
    """Record 'in_progress' before a pull and 'complete' after it."""
    state = _read_pull_state(csv_path)
    state[f"{committee_id}/{period}"] = status
    _pull_state_path(csv_path).write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


# pull and append one committee's new Schedule A filings
def run(committee_id: str, period: int, full: bool = False) -> None:
    """Append one committee's new Schedule A filings to contributions.csv."""
    api_key = required_env("FEC_API_KEY")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    csv_path = RAW_CSV
    existing_ids, latest_date = read_committee_state(
        csv_path,
        committee_id,
        period,
    )
    # pages come newest first: a pull cut off part-way has the newest rows but
    # not the older pages, so starting again from the newest saved date would
    # skip them for good. Such a pull is redone in full (deduped by sub_id).
    if _read_pull_state(csv_path).get(f"{committee_id}/{period}") == "in_progress" and not full:
        log.warning("[RESUME] the last pull of %s / %s did not finish: rechecking the whole period",
                    committee_id, period)
        full = True
    _log_pull_mode(full, period, latest_date, len(existing_ids))

    params = _pull_params(api_key, committee_id, period, latest_date, full)
    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    stats = {"new_rows": 0, "duplicates": 0, "missing_ids": 0, "pages": 0}

    log.info("[START] %s / %s -> %s", committee_id, period, csv_path)
    _mark_pull(csv_path, committee_id, period, "in_progress")
    try:
        _pull_pages(
            csv_path,
            session,
            params,
            limiter,
            existing_ids,
            committee_id,
            period,
            stats,
        )
    except KeyboardInterrupt:
        _log_summary(
            "STOPPED",
            committee_id,
            period,
            stats,
            csv_path,
            log.warning,
        )
        raise

    _mark_pull(csv_path, committee_id, period, "complete")
    _log_summary("DONE", committee_id, period, stats, csv_path, log.info)


# log a one-line summary of a pull run's outcome
def _log_summary(
    status,
    committee_id,
    period,
    stats,
    csv_path,
    writer,
) -> None:
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
