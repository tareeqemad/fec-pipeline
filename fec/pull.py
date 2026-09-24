"""Pull raw Schedule A contributions from the FEC API."""

from __future__ import annotations

import csv
import json
import logging
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from time import monotonic, sleep
from typing import Any, Iterator

import requests
from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout
from tqdm import tqdm

from fec.env import RAW_CSV

log = logging.getLogger(__name__)

BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_ATTEMPTS = 6
RATE_LIMIT_MAX_WAIT = 65 * 60
TRANSIENT_STATUSES = {500, 502, 503, 504}

FIELDS = [
    "sub_id",
    "transaction_id",
    "two_year_transaction_period",
    "committee_id",
    "contributor_name",
    "contributor_first_name",
    "contributor_last_name",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "contributor_employer",
    "contributor_occupation",
    "is_individual",
    "contribution_receipt_date",
    "contribution_receipt_amount",
]

# contributor_year preserves the existing raw CSV contract.
COLUMNS = FIELDS[:-1] + ["contributor_year"] + FIELDS[-1:]


class PullError(RuntimeError):
    """A pull cannot continue safely."""


class RateLimiter:
    """Keep request starts within the configured requests-per-minute limit."""

    def __init__(self, rpm: int = 15):
        self.interval = 60.0 / max(1, int(rpm))
        self.last_request = 0.0

    def wait(self) -> None:
        delay = self.last_request + self.interval - monotonic()
        if delay > 0:
            sleep(delay)
        self.last_request = monotonic()


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise PullError(f"missing env {name} - check .env file")
    return value


def build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "fec-pull/1.0",
            "Accept-Encoding": "gzip, deflate",
        }
    )
    return session


def _retry_delay(attempt: int, response=None, cap: float = 60.0) -> float:
    retry_after = response.headers.get("Retry-After") if response is not None else None
    fallback = min(cap, 2 ** (attempt - 1))
    if not retry_after:
        return fallback
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return fallback


def _retry_connection(error: Exception, attempt: int) -> None:
    if attempt == MAX_ATTEMPTS:
        raise PullError(f"FEC API unavailable after {attempt} attempts") from error
    delay = _retry_delay(attempt)
    log.warning("%s; retrying in %.1fs", type(error).__name__, delay)
    sleep(delay)


def _wait_for_rate_limit(response, attempt: int, waited: float) -> float:
    delay = max(1.0, _retry_delay(attempt, response, cap=120.0))
    if waited + delay > RATE_LIMIT_MAX_WAIT:
        raise PullError(f"FEC rate limit did not clear after {waited / 60:.0f} minutes")
    waited += delay
    log.warning(
        "FEC rate limit; retrying in %.1fs (waited %.1f min)",
        delay,
        waited / 60,
    )
    sleep(delay)
    return waited


def _retry_server_error(response, attempt: int) -> None:
    if attempt == MAX_ATTEMPTS:
        raise PullError(
            f"FEC API returned HTTP {response.status_code} after {attempt} attempts"
        )
    delay = _retry_delay(attempt, response)
    log.warning("HTTP %s; retrying in %.1fs", response.status_code, delay)
    sleep(delay)


def _response_data(response, params: dict) -> dict:
    if response.status_code == 403:
        raise PullError("FEC API key is invalid or expired")
    if response.status_code == 404:
        raise PullError("FEC endpoint or committee was not found")
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        message = str(error).replace(params.get("api_key", ""), "***")
        raise PullError(message) from error
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise PullError("FEC API returned invalid JSON") from error


def fetch_page(session, params: dict, limiter: RateLimiter) -> dict:
    """Fetch one page, waiting through an hourly FEC rate-limit window."""
    transient_attempts = 0
    rate_limit_attempts = 0
    rate_limit_waited = 0.0

    while True:
        limiter.wait()
        try:
            response = session.get(BASE_URL, params=params, timeout=(10, 180))
        except (ReadTimeout, ConnectTimeout, ConnectionError) as error:
            transient_attempts += 1
            _retry_connection(error, transient_attempts)
            continue

        over_limit = response.status_code == 429 or "OVER_RATE_LIMIT" in response.text
        if over_limit:
            rate_limit_attempts += 1
            rate_limit_waited = _wait_for_rate_limit(
                response,
                rate_limit_attempts,
                rate_limit_waited,
            )
            continue

        if response.status_code in TRANSIENT_STATUSES:
            transient_attempts += 1
            _retry_server_error(response, transient_attempts)
            continue

        return _response_data(response, params)


def _number(value) -> Decimal:
    try:
        return Decimal(str(value).strip() or "0") if value is not None else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _date(value) -> str | None:
    return str(value)[:10] if value and str(value).strip() else None


def _year(date_value: str | None) -> int | None:
    try:
        return int(date_value[:4]) if date_value else None
    except ValueError:
        return None


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


def build_row(result: dict[str, Any]) -> list | None:
    """Convert one FEC result to the raw CSV schema."""
    sub_id = str(result.get("sub_id") or "").strip()
    if not sub_id:
        return None

    receipt_date = _date(result.get("contribution_receipt_date"))
    values = {field: result.get(field) for field in FIELDS}
    values["sub_id"] = sub_id
    values["contribution_receipt_date"] = receipt_date
    values["contributor_year"] = _year(receipt_date)
    values["contribution_receipt_amount"] = _number(
        result.get("contribution_receipt_amount")
    )
    return [values.get(column) for column in COLUMNS]


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


def _pull_state_path(csv_path: Path) -> Path:
    return csv_path.parent / "pull_state.json"


def _read_pull_state(csv_path: Path) -> dict:
    path = _pull_state_path(csv_path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _mark_pull(csv_path: Path, committee_id: str, period: int, status: str) -> None:
    """Record 'in_progress' before a pull and 'complete' after it."""
    state = _read_pull_state(csv_path)
    state[f"{committee_id}/{period}"] = status
    _pull_state_path(csv_path).write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


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
