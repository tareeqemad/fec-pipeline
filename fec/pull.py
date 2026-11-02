"""Save complete, dated snapshots of FEC's processed Schedule A receipts."""
from __future__ import annotations

import csv
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic_ns

from fec.env import RAW_CSV
from fec.fec_api import PullError, RateLimiter, build_session, fetch_page, required_env
from fec.pull_rows import ENTITY_FIELDS, FIELDS, LINE_FIELDS, SNAPSHOT_COLUMNS, build_row, source_values
from fec.receipt_types import classify_receipt

log = logging.getLogger(__name__)


def iter_pages(session, params: dict, limiter: RateLimiter):
    """Follow the whole FEC cursor, including the null-date pagination flag."""
    cursor = {}
    seen = set()
    while True:
        page = fetch_page(session, {**cursor, **params}, limiter)
        if not isinstance(page, dict) or not isinstance(page.get("results"), list):
            raise PullError("Invalid Schedule A response: expected a list of results")
        pagination = page.get("pagination")
        if not isinstance(pagination, dict):
            raise PullError("Missing Schedule A pagination")
        if not page["results"]:
            yield page
            return

        cursor = pagination.get("last_indexes")
        if not isinstance(cursor, dict) or not cursor.get("last_index") or not (
            cursor.get("last_contribution_receipt_date") or cursor.get("sort_null_only")
        ):
            raise PullError("Incomplete Schedule A cursor")
        position = tuple(sorted(cursor.items()))
        if position in seen:
            raise PullError("Schedule A returned a repeated cursor")
        seen.add(position)
        yield page


def save_snapshot(raw_path: Path, committee: str, period: int, pages) -> Path:
    """Publish one scope only after all pages have been validated."""
    started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    # The system clock can give consecutive scans the same UTC timestamp.
    started = f"{started}-{monotonic_ns():020d}"
    rows = {}
    exact_count = None
    for page in pages:
        pagination = page["pagination"]
        if pagination.get("is_count_exact"):
            count = pagination["count"]
            if exact_count is not None and count != exact_count:
                raise PullError("FEC count changed during the scan; retry this scope")
            exact_count = count
        if not page["results"]:
            break
        for result in page["results"]:
            if result.get("committee_id") != committee or str(result.get("two_year_transaction_period")) != str(period):
                raise PullError("FEC returned a filing outside the requested committee or period")
            row = build_row(result)
            receipt = (row, source_values(result))
            sid = row[0]
            if sid in rows and rows[sid] != receipt:
                raise PullError(f"Filing {sid} changed during the scan; retry this scope")
            rows[sid] = receipt
    else:
        raise PullError("Incomplete scan: no terminal page")
    if exact_count is not None and len(rows) != exact_count:
        raise PullError(f"Incomplete scan: received {len(rows)} unique filings, expected {exact_count}")

    folder = raw_path.parent / "snapshots" / f"{committee}-{period}"
    folder.mkdir(parents=True, exist_ok=True)
    handle = NamedTemporaryFile(dir=folder, prefix=started + "-", suffix=".tmp", delete=False,
                                mode="w", encoding="utf-8", newline="")
    pending = Path(handle.name)
    snapshot = pending.with_suffix(".csv")
    try:
        with handle:
            writer = csv.writer(handle)
            writer.writerow(SNAPSHOT_COLUMNS)
            for row, fields in rows.values():
                kind = classify_receipt(*(fields[key] for key in LINE_FIELDS))
                writer.writerow([*row, *(fields[key] for key in ENTITY_FIELDS), kind])
        os.replace(pending, snapshot)
    finally:
        pending.unlink(missing_ok=True)
    log.info("Saved %s current filings for %s/%s", f"{len(rows):,}", committee, period)
    return snapshot


def run(committee_id: str, period: int) -> None:
    params = {
        "api_key": required_env("FEC_API_KEY"),
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(FIELDS),
    }
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    with build_session() as session:
        save_snapshot(RAW_CSV, committee_id, period, iter_pages(session, params, limiter))
