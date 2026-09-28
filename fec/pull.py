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

from fec.env import FEC_SOURCE_CSV, RAW_CSV
from fec.fec_api import RateLimiter, build_session, fetch_page, required_env
from fec.io import replace_file
from fec.pull_rows import COLUMNS, FIELDS, SOURCE_COLUMNS, build_row, source_values

log = logging.getLogger(__name__)


# data/raw/fec_source_fields.csv beside the raw file it describes
def source_path(csv_path: Path) -> Path:
    return csv_path.parent / FEC_SOURCE_CSV.name


# FEC source fields already saved, by raw sub_id
def read_source_fields(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return {row["sub_id"]: row for row in csv.DictReader(handle) if row.get("sub_id")}


# add source records to the file, writing its header when new
def append_source_fields(path: Path, records: list[dict]) -> None:
    if not records:
        return
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS, restval="")
        if new:
            writer.writeheader()
        writer.writerows(records)


# replace the source file with these records, atomically
def write_source_fields(path: Path, records: dict[str, dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS, restval="", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records[sub_id] for sub_id in sorted(records))
    replace_file(str(tmp), str(path))


# one source record for a raw row, from FEC's result
def _source_record(sub_id: str, result: dict, matched_by: str) -> dict:
    fec_sub_id = str(result.get("sub_id") or "").strip()
    return {"sub_id": sub_id, **source_values(result), "matched_by": matched_by,
            "fec_sub_id": fec_sub_id if fec_sub_id != sub_id else ""}


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

                sources = []
                for result in data.get("results", []):
                    row = build_row(result)
                    if row is None:
                        stats["missing_ids"] += 1
                    elif str(row[0]) in existing_ids:
                        stats["duplicates"] += 1
                    else:
                        writer.writerow(row)
                        existing_ids.add(str(row[0]))
                        sources.append(_source_record(str(row[0]), result, "pulled"))
                        stats["new_rows"] += 1
                handle.flush()
                append_source_fields(source_path(csv_path), sources)
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


# match the rows already in the raw file to FEC's current results
def match_source_fields(rows: list[dict], results: list[dict], committee_id: str,
                        period: int) -> tuple[dict[str, dict], dict]:
    """FEC's own entity type and contributor id for each raw row of this committee period.

    A row is matched by its sub_id; when FEC now returns that transaction
    under a newer sub_id (an amended report), by one exact match on
    committee, transaction id, date and amount, on both sides. Never by
    name. The raw rows are only read.
    """
    stats = {"by_sub_id": 0, "by_transaction": 0, "ambiguous": 0, "unmatched": 0}
    by_sub_id = {str(result.get("sub_id") or "").strip(): result for result in results}
    mine = [row for row in rows if row.get("committee_id") == committee_id
            and row.get("two_year_transaction_period") == str(period)]
    known = {row.get("sub_id") for row in mine}
    loose: dict[tuple, list] = {}
    for sub_id, result in by_sub_id.items():
        if sub_id and sub_id not in known:
            loose.setdefault(_transaction_key(result), []).append(result)
    records: dict[str, dict] = {}
    unmatched: dict[tuple, list] = {}
    for row in mine:
        sub_id = row.get("sub_id", "")
        if sub_id in by_sub_id:
            records[sub_id] = _source_record(sub_id, by_sub_id[sub_id], "sub_id")
            stats["by_sub_id"] += 1
        else:
            unmatched.setdefault(_transaction_key(row), []).append(row)
    for key, group in unmatched.items():
        candidates = loose.get(key, [])
        if len(group) == 1 and len(candidates) == 1 and key[1]:
            sub_id = group[0]["sub_id"]
            records[sub_id] = _source_record(sub_id, candidates[0], "transaction")
            stats["by_transaction"] += 1
        else:
            stats["ambiguous" if candidates else "unmatched"] += len(group)
    return records, stats


# walk a whole committee period and save the source fields of existing rows
def backfill_source(committee_id: str, period: int) -> dict:
    """Write fec_source_fields.csv for rows already in contributions.csv, which is only read."""
    api_key = required_env("FEC_API_KEY")
    csv_path = RAW_CSV
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)
    params = _pull_params(api_key, committee_id, period, None, full=True)
    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    results: list[dict] = []
    log.info("[BACKFILL] %s / %s: reading every page (the raw file is not changed)", committee_id, period)
    for data in iter_pages(session, params, limiter):
        results.extend(data.get("results", []))
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    records, stats = match_source_fields(rows, results, committee_id, period)
    path = source_path(csv_path)
    saved = read_source_fields(path)
    stats["changed"] = sum(
        1 for sub_id, record in records.items()
        if sub_id in saved and any(saved[sub_id].get(k, "") != record[k] for k in ("fec_entity_type", "fec_contributor_id"))
    )
    write_source_fields(path, {**saved, **records})
    log.info(
        "[BACKFILL] %s / %s: %s results | by sub_id %s | by transaction %s | changed %s | "
        "ambiguous %s | unmatched %s -> %s",
        committee_id, period, f"{len(results):,}", *(f"{stats[k]:,}" for k in
        ("by_sub_id", "by_transaction", "changed", "ambiguous", "unmatched")), path,
    )
    return stats
