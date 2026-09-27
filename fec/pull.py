"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import json
import logging
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

from tqdm import tqdm

from fec.env import RAW_CSV
from fec.fec_api import RateLimiter, build_session, fetch_page, required_env
from fec.pull_rows import COLUMNS, FIELDS, build_row, source_values

log = logging.getLogger(__name__)


# give an older raw file the columns this pull writes
def ensure_columns(csv_path: Path) -> int:
    """Rewrite the file with any missing trailing columns, empty; returns rows.

    A raw file written before the FEC source fields existed has every other
    column in the same order: they are only added, nothing else changes.
    Any other difference in the header stops the pull.
    """
    if not csv_path.exists():
        return 0
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        header = next(csv.reader(handle), [])
    if header == COLUMNS:
        return 0
    if header != COLUMNS[:len(header)]:
        raise ValueError(f"{csv_path} header is not a prefix of {COLUMNS}: {header}")
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    _write_rows(csv_path, rows)
    log.info("[COLUMNS] added %s to %s (%s rows)", ", ".join(COLUMNS[len(header):]), csv_path, f"{len(rows):,}")
    return len(rows)


# write raw rows to the CSV in place, atomically
def _write_rows(csv_path: Path, rows: list[dict]) -> None:
    tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, restval="")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, csv_path)


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
    ensure_columns(csv_path)
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


# the key two versions of one transaction share: its committee, id, date and amount
def _transaction_key(row: dict) -> tuple:
    try:
        amount = Decimal(str(row.get("contribution_receipt_amount") or "0").strip() or "0")
    except ArithmeticError:
        amount = None
    return (
        str(row.get("committee_id") or "").strip(),
        str(row.get("transaction_id") or "").strip(),
        str(row.get("contribution_receipt_date") or "")[:10],
        amount,
    )


# fill the FEC source fields of rows already in the raw file
def fill_source_fields(rows: list[dict], results: list[dict], committee_id: str, period: int) -> dict:
    """Give each existing row of this committee period FEC's own entity type and contributor id.

    A row is matched by its sub_id; when FEC now returns that transaction
    under a newer sub_id (an amended report), by one exact match on
    committee, transaction id, date and amount, on both sides. Never by
    name. Nothing is appended: new filings come from a normal pull.
    """
    stats = {"by_sub_id": 0, "by_transaction": 0, "changed": 0, "unmatched": 0, "ambiguous": 0}
    by_sub_id = {str(result.get("sub_id") or "").strip(): result for result in results}
    mine = [row for row in rows if row.get("committee_id") == committee_id
            and row.get("two_year_transaction_period") == str(period)]
    known = {row.get("sub_id") for row in mine}
    loose: dict[tuple, list] = {}
    for sub_id, result in by_sub_id.items():
        if sub_id and sub_id not in known:
            loose.setdefault(_transaction_key(result), []).append(result)
    unmatched: dict[tuple, list] = {}
    for row in mine:
        if row.get("sub_id") in by_sub_id:
            _set_source(row, by_sub_id[row["sub_id"]], stats)
            stats["by_sub_id"] += 1
        else:
            unmatched.setdefault(_transaction_key(row), []).append(row)
    for key, group in unmatched.items():
        candidates = loose.get(key, [])
        if len(group) == 1 and len(candidates) == 1 and key[1]:
            _set_source(group[0], candidates[0], stats)
            stats["by_transaction"] += 1
        else:
            stats["ambiguous" if candidates else "unmatched"] += len(group)
    return stats


# copy one result's source fields onto a row, counting real changes
def _set_source(row: dict, result: dict, stats: dict) -> None:
    values = source_values(result)
    if any(row.get(column) and row.get(column) != value for column, value in values.items()):
        stats["changed"] += 1
    row.update(values)


# walk a whole committee period and fill the source fields of existing rows
def backfill_source(committee_id: str, period: int) -> dict:
    """Fill fec_entity_type / fec_contributor_id for rows already in contributions.csv."""
    api_key = required_env("FEC_API_KEY")
    csv_path = RAW_CSV
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    ensure_columns(csv_path)
    params = _pull_params(api_key, committee_id, period, None, full=True)
    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    results: list[dict] = []
    log.info("[BACKFILL] %s / %s: reading every page (nothing is appended)", committee_id, period)
    for data in iter_pages(session, params, limiter):
        results.extend(data.get("results", []))
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    stats = fill_source_fields(rows, results, committee_id, period)
    _write_rows(csv_path, rows)
    log.info(
        "[BACKFILL] %s / %s: %s results | by sub_id %s | by transaction %s | changed %s | "
        "ambiguous %s | unmatched %s",
        committee_id, period, f"{len(results):,}", *(f"{stats[k]:,}" for k in
        ("by_sub_id", "by_transaction", "changed", "ambiguous", "unmatched")),
    )
    return stats
