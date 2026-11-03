"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Iterable, Iterator

from tqdm import tqdm

from fec.env import FEC_SOURCE_CSV, RAW_CSV
from fec.fec_api import PullError, RateLimiter, build_session, fetch_page, required_env
from fec.io import replace_file
from fec.pull_rows import COLUMNS, FIELDS, SOURCE_COLUMNS, build_row, source_values

log = logging.getLogger(__name__)


# Keep source fields beside the raw CSV.
def source_path(csv_path: Path) -> Path:
    return csv_path.parent / FEC_SOURCE_CSV.name


# Keep source fields for a pulled filing.
def _source_record(sub_id: str, result: dict) -> dict:
    return {"sub_id": sub_id, **source_values(result), "matched_by": "pulled", "fec_sub_id": ""}


# ActBlue's memo repeats the donor's own earmarked gift.
def _repeats_a_gift(result: dict) -> bool:
    memo = str(result.get("memo_code") or "").strip().upper()
    return memo == "X" and str(result.get("contributor_name") or "").strip().upper() == "ACTBLUE"


# Follow FEC pagination cursors until an empty page.
def iter_pages(session, params: dict, limiter: RateLimiter) -> Iterator[dict]:
    params = dict(params)
    seen = set()
    while True:
        data = fetch_page(session, params, limiter)
        yield data

        if not data.get("results"):
            return
        indexes = (data.get("pagination") or {}).get("last_indexes")
        if not indexes or tuple(sorted(indexes.items())) in seen:
            raise PullError("FEC stopped before the last page; nothing was saved")
        seen.add(tuple(sorted(indexes.items())))
        params.update(indexes)


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


# Read every page first; a partial scan raises and saves nothing.
def _read_all_pages(pages: Iterable[dict]) -> tuple[dict, dict, int]:
    results = {}
    expected = None
    progress = None
    try:
        for data in pages:
            pagination = data.get("pagination") or {}
            if progress is None and pagination.get("count"):
                progress = tqdm(total=-(-pagination["count"] // 100), unit="pg")
            if progress:
                progress.update(1)
            if pagination.get("is_count_exact"):
                expected = pagination.get("count")
            for result in data.get("results", []):
                sub_id = str(result.get("sub_id") or "").strip()
                if not sub_id:
                    raise PullError("FEC returned a filing without a sub_id; nothing was saved")
                results[sub_id] = result
    finally:
        if progress:
            progress.close()
    if expected is not None and len(results) != expected:
        raise PullError(f"Received {len(results):,} of {expected:,} filings; nothing was saved")
    kept = {sub_id: result for sub_id, result in results.items() if not _repeats_a_gift(result)}
    return kept, {sub_id: _source_record(sub_id, result) for sub_id, result in kept.items()}, len(results) - len(kept)


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, columns: list[str], rows: Iterable[dict]) -> None:
    temp = path.with_name(path.name + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)
    replace_file(str(temp), str(path))


# Replace one committee and period with FEC's current filings.
def run(committee_id: str, period: int) -> None:
    api_key = required_env("FEC_API_KEY")
    RAW_CSV.parent.mkdir(parents=True, exist_ok=True)
    params = _pull_params(api_key, committee_id, period)
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    log.info("[START] %s / %s -> %s", committee_id, period, RAW_CSV)

    results, sources, skipped = _read_all_pages(iter_pages(build_session(), params, limiter))

    saved = _read_rows(RAW_CSV)
    in_scope = {
        row["sub_id"] for row in saved
        if row["committee_id"] == committee_id and row["two_year_transaction_period"] == str(period)
    }
    if in_scope and not results:
        raise PullError(f"FEC returned no filings for {committee_id}/{period}; nothing was saved")

    new_rows = [dict(zip(COLUMNS, build_row(result))) for result in results.values()]
    other_rows = [row for row in saved if row["sub_id"] not in in_scope]
    other_sources = [row for row in _read_rows(source_path(RAW_CSV)) if row["sub_id"] not in in_scope | set(sources)]
    # Save source fields first, so filings always have them.
    _write_rows(source_path(RAW_CSV), SOURCE_COLUMNS, [*other_sources, *sources.values()])
    _write_rows(RAW_CSV, COLUMNS, [*other_rows, *new_rows])
    log.info(
        "[DONE] %s / %s: current=%s added=%s removed=%s skipped_actblue_memos=%s",
        committee_id, period, f"{len(new_rows):,}", f"{len(set(results) - in_scope):,}",
        f"{len(in_scope - set(results)):,}", f"{skipped:,}",
    )
