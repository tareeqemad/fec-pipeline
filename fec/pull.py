"""FEC Schedule A pull: rate limiting, page fetching, output schema, and the per-committee run() that appends to data/contributions.csv."""
import csv
import logging
import os
import random
import sys
from decimal import Decimal
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ReadTimeout, ConnectTimeout, ConnectionError
from tqdm import tqdm
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"


class RateLimiter:
    def __init__(self, rpm: int = 15):
        self.interval = 60.0 / max(1, int(rpm))
        self._last = 0.0
        self.backoff = 1.0

    def before_request(self):
        wait = (self._last + self.interval) - monotonic()
        if wait > 0:
            sleep(wait)

    def after_success(self):
        self._last = monotonic()
        self.backoff = max(1.0, self.backoff * 0.5)

    def after_rate_limit(self, retry_after=None):
        wait = float(retry_after) if retry_after and retry_after > 0 else min(120.0, 2.0 * self.backoff)
        sleep(wait)
        self._last = monotonic()
        self.backoff = min(60.0, self.backoff * 2.0)


def env_or_die(name: str) -> str:
    v = os.getenv(name)
    if not v:
        log.error(f"missing env {name} - check .env file")
        sys.exit(2)
    return v


def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=5, connect=3, read=3, backoff_factor=1.0,
                  status_forcelist=[500, 502, 503, 504], allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = "fec-pull/1.0"
    s.headers["Accept-Encoding"] = "gzip, deflate"
    return s


def fetch_page(session, params, limiter):
    retries = 0
    while True:
        limiter.before_request()
        log.debug(f"GET {','.join(f'{k}={v}' for k,v in params.items() if k != 'api_key')}")
        try:
            resp = session.get(BASE_URL, params=params, timeout=(10, 180))
        except (ReadTimeout, ConnectTimeout, ConnectionError) as e:
            wait = min(60.0, (2 ** retries)) + random.uniform(0, 0.5)
            log.warning(f"{type(e).__name__}. sleeping {wait:.1f}s")
            sleep(wait)
            retries += 1
            limiter.after_rate_limit(wait)
            continue

        if resp.status_code == 429 or "OVER_RATE_LIMIT" in resp.text:
            ra = resp.headers.get("Retry-After")
            ra = float(ra) if ra else None
            log.warning(f"429 rate limited. Retry-After={ra}")
            limiter.after_rate_limit(ra)
            continue

        if resp.status_code in (500, 502, 503, 504):
            wait = min(60.0, 2.0 * (2 ** retries))
            log.warning(f"HTTP {resp.status_code}. sleeping {wait:.1f}s")
            sleep(wait)
            retries += 1
            continue

        if resp.status_code == 403:
            log.error("403 Forbidden - API key invalid or expired. "
                      "Get a new one: https://api.open.fec.gov/developers/")
            sys.exit(1)

        if resp.status_code == 404:
            log.error("404 Not Found - check committee_id")
            sys.exit(1)

        # hide the api key in error messages
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            msg = str(e).replace(params.get("api_key", ""), "***")
            log.error(f"HTTP error: {msg}")
            raise
        limiter.after_success()
        return resp.json()


# lowercase 'data' matches fec.env / clean.py; "DATA" silently became a separate dir on case-sensitive filesystems
DATA_DIR = Path("data")

FIELDS = [
    "sub_id", "transaction_id", "two_year_transaction_period",
    "committee_id",
    "contributor_name", "contributor_first_name", "contributor_last_name",
    "contributor_street_1", "contributor_street_2",
    "contributor_city", "contributor_state", "contributor_zip",
    "contributor_employer", "contributor_occupation",
    "is_individual",
    "contribution_receipt_date", "contribution_receipt_amount",
]

# CSV columns = the API fields plus the derived year, kept right before the
# amount; build_row() writes values in exactly this order
COLUMNS = FIELDS[:-1] + ["contributor_year"] + FIELDS[-1:]


def _num(v):
    if v is None:
        return Decimal("0")
    try:
        return Decimal(str(v).strip() or "0")
    except Exception:
        return Decimal("0")


def _date(v):
    if not v or (isinstance(v, str) and not v.strip()):
        return None
    return str(v)[:10]


def _year(date_str):
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except Exception:
        return None


def read_committee_state(csv_path: Path, committee_id: str) -> tuple[set, str | None]:
    """One pass over the CSV: this committee's sub_ids (for dedup) and its latest receipt date (for refresh)."""
    sub_ids: set = set()
    latest_date = None
    if not csv_path.exists():
        return sub_ids, latest_date
    with open(csv_path, "r", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("committee_id") != committee_id:
                continue
            sub_ids.add(row.get("sub_id"))
            receipt_date = row.get("contribution_receipt_date")
            if receipt_date and (latest_date is None or receipt_date > latest_date):
                latest_date = receipt_date
    return sub_ids, latest_date


def build_row(r: dict[str, Any]) -> list | None:
    sub_id = r.get("sub_id")
    if sub_id is None:
        return None
    receipt_date = _date(r.get("contribution_receipt_date"))
    return [
        sub_id,
        r.get("transaction_id"),
        r.get("two_year_transaction_period"),
        r.get("committee_id"),
        r.get("contributor_name"),
        r.get("contributor_first_name"),
        r.get("contributor_last_name"),
        r.get("contributor_street_1"),
        r.get("contributor_street_2"),
        r.get("contributor_city"),
        r.get("contributor_state"),
        r.get("contributor_zip"),
        r.get("contributor_employer"),
        r.get("contributor_occupation"),
        r.get("is_individual"),
        receipt_date,
        _year(receipt_date),
        _num(r.get("contribution_receipt_amount")),
    ]


def run(committee_id: str, period: int, full: bool = False) -> None:
    """Pull one committee's receipts into data/contributions.csv, skipping rows already there; full=True re-pulls the whole period (dedup by sub_id)."""
    api_key = env_or_die("FEC_API_KEY")
    DATA_DIR.mkdir(exist_ok=True)
    csv_path = DATA_DIR / "contributions.csv"

    session = build_session()
    limiter = RateLimiter(rpm=int(os.getenv("FEC_RPM", "15")))
    existing_ids, latest_date = read_committee_state(csv_path, committee_id)
    write_header = not csv_path.exists()

    min_date = None
    if full:
        # unbounded re-pull + sub_id dedup catches late/amended filings the refresh mode misses
        log.info(f"[FULL] re-pulling the entire {period} period (dedup by sub_id)")
    elif latest_date:
        min_date = latest_date
        log.info(f"[REFRESH] {len(existing_ids):,} existing rows, pulling records after {min_date}")
    else:
        log.info("[REFRESH] no existing data -> pulling everything")

    params = {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": 100,
        "fields": ",".join(FIELDS),
    }
    if min_date:
        params["min_date"] = min_date

    log.info(f"[START] {committee_id} / {period} -> {csv_path}")

    total_new = 0
    total_skipped = 0
    total_no_id = 0
    page = 0
    pbar = None

    try:
        with open(csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if write_header:
                writer.writerow(COLUMNS)

            while True:
                data = fetch_page(session, params, limiter)

                if pbar is None:
                    count = (data.get("pagination") or {}).get("count")
                    if count:
                        pbar = tqdm(total=-(-count // 100),
                                    desc=f"{committee_id}/{period}", unit="pg")

                results = data.get("results", [])
                page += 1
                if pbar:
                    pbar.update(1)
                if not results:
                    break

                for r in results:
                    row = build_row(r)
                    if row is None:
                        total_no_id += 1
                        continue
                    if str(row[0]) in existing_ids:
                        total_skipped += 1
                        continue
                    writer.writerow(row)
                    existing_ids.add(str(row[0]))
                    total_new += 1

                f.flush()

                last_indexes = (data.get("pagination") or {}).get("last_indexes")
                if not last_indexes:
                    break
                params["last_contribution_receipt_date"] = (
                    last_indexes.get("last_contribution_receipt_date"))
                params["last_index"] = last_indexes.get("last_index")

    except KeyboardInterrupt:
        log.warning("Interrupted - data saved so far is safe")

    finally:
        if pbar:
            pbar.close()

    log.info(
        f"\n{'='*60}\n"
        f"  {committee_id} / {period}\n"
        f"  new rows   = {total_new:,}\n"
        f"  skipped    = {total_skipped:,} (duplicates)\n"
        f"  no sub_id  = {total_no_id:,}\n"
        f"  pages      = {page}\n"
        f"  file       = {csv_path}\n"
        f"{'='*60}"
    )
