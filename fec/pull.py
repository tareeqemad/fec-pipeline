"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

from fec.env import FEC_SOURCE_CSV, RAW_CSV
from fec.fec_api import RateLimiter, build_session, fetch_page, required_env
from fec.pull_rows import COLUMNS, FIELDS, SOURCE_COLUMNS, build_row, source_values

log = logging.getLogger(__name__)


# Keep source fields beside the raw CSV.
def source_path(csv_path: Path) -> Path:
    return csv_path.parent / FEC_SOURCE_CSV.name


# Append source fields, adding the CSV header when needed.
def append_source_fields(path: Path, records: list[dict]) -> None:
    if not records:
        return
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_COLUMNS, restval="")
        if new:
            writer.writeheader()
        writer.writerows(records)


# Keep source fields for a newly pulled filing.
def _source_record(sub_id: str, result: dict) -> dict:
    return {"sub_id": sub_id, **source_values(result), "matched_by": "pulled", "fec_sub_id": ""}


# Find saved filing IDs for this committee and period.
def read_existing_ids(
    csv_path: Path,
    committee_id: str,
    period: int,
) -> set[str]:
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


# Scan the full period and append only new filing IDs.
def run(committee_id: str, period: int) -> None:
    api_key = required_env("FEC_API_KEY")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    csv_path = RAW_CSV
    existing_ids = read_existing_ids(csv_path, committee_id, period)
    log.info("[SCAN] period %s; skipping %s saved sub_id values", period, f"{len(existing_ids):,}")

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

                    sources = []
                    # Validate this page before saving any of its rows.
                    page = []
                    page_ids = set()
                    for result in data.get("results", []):
                        sub_id = str(result.get("sub_id") or "").strip()
                        if not sub_id:
                            stats["missing_ids"] += 1
                            continue
                        if sub_id in existing_ids or sub_id in page_ids:
                            stats["duplicates"] += 1
                            continue
                        page.append(build_row(result))
                        page_ids.add(sub_id)
                        sources.append(_source_record(sub_id, result))
                    # Save metadata first so saved filings always have their source fields.
                    append_source_fields(source_path(csv_path), sources)
                    writer.writerows(page)
                    handle.flush()
                    existing_ids.update(page_ids)
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
