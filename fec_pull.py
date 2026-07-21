# fec_pull.py
"""
FEC Schedule A → CSV (no database required)
data/contributions.csv — ملف واحد لكل اللجان

Usage:
  python fec_pull.py --committee-id C00797670 --period 2026
  python fec_pull.py --committee-id C00401224 --period 2026   # يكمل على نفس الملف

Resume: يقرأ آخر تاريخ للجنة من CSV ويكمل من بعده تلقائياً
"""
import os, sys, argparse, logging, csv
from pathlib import Path
from time import monotonic, sleep
from typing import Dict, Any, Optional, List
from decimal import Decimal
import random

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import ReadTimeout, ConnectTimeout, ConnectionError
from urllib3.util.retry import Retry
from dotenv import load_dotenv
from tqdm import tqdm

from fec.log import get_logger

logger = get_logger(__name__)


load_dotenv()

log = logging.getLogger("fec_pull")

BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
# lowercase 'data' — matches fec.env / clean.py. Was "DATA", which
# silently became a SEPARATE dir on case-sensitive filesystems (Linux), so the
# cleaner (reads data/) wouldn't see freshly-pulled rows.
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

COLUMNS = [
    "sub_id", "transaction_id", "two_year_transaction_period",
    "committee_id",
    "contributor_name", "contributor_first_name", "contributor_last_name",
    "contributor_street_1", "contributor_street_2",
    "contributor_city", "contributor_state", "contributor_zip",
    "contributor_employer", "contributor_occupation",
    "is_individual",
    "contribution_receipt_date", "contributor_year",
    "contribution_receipt_amount",
]


# ═══════════════════════════════════════════
#  Rate Limiter
# ═══════════════════════════════════════════

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


# ═══════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════

def env_or_die(name: str) -> str:
    v = os.getenv(name)
    if not v:
        log.error(f"missing env {name} — check .env file")
        sys.exit(2)
    return v


def _num(v):
    if v is None: return Decimal("0")
    try: return Decimal(str(v).strip() or "0")
    except Exception: return Decimal("0")


def _date(v):
    if not v or (isinstance(v, str) and not v.strip()): return None
    return str(v)[:10]


def _year(date_str):
    if not date_str: return None
    try: return int(str(date_str)[:4])
    except Exception: return None


# ═══════════════════════════════════════════
#  Resume: read last date for committee from CSV
# ═══════════════════════════════════════════

def get_existing_sub_ids(csv_path: Path, committee_id: str) -> set:
    """Read existing sub_ids for this committee to avoid duplicates."""
    if not csv_path.exists():
        return set()
    ids = set()
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("committee_id") == committee_id:
                ids.add(row.get("sub_id"))
    return ids


def get_resume_date(csv_path: Path, committee_id: str) -> Optional[str]:
    """Find the earliest contribution_receipt_date for this committee."""
    if not csv_path.exists():
        return None
    earliest = None
    count = 0
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("committee_id") == committee_id:
                count += 1
                d = row.get("contribution_receipt_date")
                if d:
                    if earliest is None or d < earliest:
                        earliest = d
    if count > 0:
        log.info(f"[RESUME] {count:,} existing rows for {committee_id}, "
                 f"earliest date={earliest}")
    return earliest


def get_latest_date(csv_path: Path, committee_id: str) -> Optional[str]:
    """Find the latest contribution_receipt_date for this committee."""
    if not csv_path.exists():
        return None
    latest = None
    count = 0
    with open(csv_path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("committee_id") == committee_id:
                count += 1
                d = row.get("contribution_receipt_date")
                if d:
                    if latest is None or d > latest:
                        latest = d
    if count > 0:
        log.info(f"[REFRESH] {count:,} existing rows for {committee_id}, "
                 f"latest date={latest}")
    return latest


# ═══════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════

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
            sleep(wait); retries += 1; limiter.after_rate_limit(wait)
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
            sleep(wait); retries += 1
            continue

        if resp.status_code == 403:
            log.error("403 Forbidden — API key invalid or expired. "
                      "Get a new one: https://api.open.fec.gov/developers/")
            sys.exit(1)

        if resp.status_code == 404:
            log.error(f"404 Not Found — check committee_id")
            sys.exit(1)

        # Hide API key from error messages
        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as e:
            msg = str(e).replace(params.get("api_key", ""), "***")
            log.error(f"HTTP error: {msg}")
            raise
        limiter.after_success()
        return resp.json()


# ═══════════════════════════════════════════
#  Row builder
# ═══════════════════════════════════════════

def build_row(r: Dict[str, Any]) -> Optional[List]:
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


# ═══════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════

def run(committee_id: str, period: int, rpm: int, per_page: int,
        min_date: Optional[str], max_date: Optional[str],
        refresh: bool = False, full: bool = False):

    api_key = env_or_die("FEC_API_KEY")
    DATA_DIR.mkdir(exist_ok=True)
    csv_path = DATA_DIR / "contributions.csv"

    session = build_session()
    limiter = RateLimiter(rpm=rpm)

    # ── Resume: check existing data ──
    existing_ids = get_existing_sub_ids(csv_path, committee_id)

    file_exists = csv_path.exists()
    write_header = not file_exists

    if full:
        # FULL mode: re-pull the WHOLE period unbounded; sub_id dedup skips
        # everything we already have, so only genuinely-missing rows are added
        # (records dated before our earliest AND after our latest — e.g. late
        # or amended filings the date-bounded refresh/resume modes can't catch).
        log.info(f"[FULL] re-pulling the entire {period} period (dedup by sub_id)")
    elif refresh and not min_date:
        # REFRESH mode: pull only records NEWER than what we have
        latest = get_latest_date(csv_path, committee_id)
        if latest:
            min_date = latest
            log.info(f"[REFRESH] pulling records after {min_date}")
        else:
            log.info(f"[REFRESH] no existing data → pulling everything")
    elif not refresh:
        # RESUME mode: pull records OLDER than what we have
        resume_date = get_resume_date(csv_path, committee_id)
        if resume_date and not max_date:
            max_date = resume_date
            log.info(f"[RESUME] pulling records before {max_date}")

    params = {
        "api_key": api_key,
        "committee_id": committee_id,
        "two_year_transaction_period": period,
        "sort": "-contribution_receipt_date",
        "per_page": max(1, min(per_page, 100)),
        "fields": ",".join(FIELDS),
    }
    if min_date: params["min_date"] = min_date
    if max_date: params["max_date"] = max_date

    log.info(f"[START] {committee_id} / {period} → {csv_path}")

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
                        pbar = tqdm(total=-(-count // params["per_page"]),
                                    desc=f"{committee_id}/{period}", unit="pg")

                results = data.get("results", [])
                page += 1
                if pbar: pbar.update(1)
                log.debug(f"page {page}: {len(results)} rows")
                if not results: break

                for r in results:
                    row = build_row(r)
                    if row is None:
                        total_no_id += 1; continue
                    if str(row[0]) in existing_ids:
                        total_skipped += 1; continue
                    writer.writerow(row)
                    existing_ids.add(str(row[0]))
                    total_new += 1

                # Flush every page
                f.flush()

                last_indexes = (data.get("pagination") or {}).get("last_indexes")
                if not last_indexes: break

                params = {
                    "api_key": api_key,
                    "committee_id": committee_id,
                    "two_year_transaction_period": period,
                    "sort": "-contribution_receipt_date",
                    "per_page": params["per_page"],
                    "fields": ",".join(FIELDS),
                    "last_contribution_receipt_date":
                        last_indexes.get("last_contribution_receipt_date"),
                    "last_index": last_indexes.get("last_index"),
                }
                if min_date: params["min_date"] = min_date
                if max_date: params["max_date"] = max_date

    except KeyboardInterrupt:
        log.warning("Interrupted — data saved so far is safe")

    finally:
        if pbar: pbar.close()

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


def main():
    ap = argparse.ArgumentParser(
        description="FEC Schedule A → CSV. Appends to DATA/contributions.csv",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python fec_pull.py --committee-id C00797670 --period 2026
  python fec_pull.py --committee-id C00401224 --period 2026
  python fec_pull.py --committee-id C00799031 --period 2026 --refresh   # pull NEW records only
  python fec_pull.py --committee-id C00797670 --period 2026 --min-date 2025-07-01

Resume (default): pulls OLDER records before the earliest existing date.
Refresh (--refresh): pulls NEWER records after the latest existing date.
Both modes skip duplicates automatically via sub_id.
        """,
    )
    ap.add_argument("--committee-id", required=True)
    ap.add_argument("--period", type=int, required=True)
    ap.add_argument("--rpm", type=int, default=int(os.getenv("FEC_RPM", "15")))
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--min-date", type=str)
    ap.add_argument("--max-date", type=str)
    ap.add_argument("--refresh", action="store_true",
                    help="Pull only NEW records after the latest existing date")
    ap.add_argument("--full", action="store_true",
                    help="Re-pull the entire period (dedup by sub_id) — catches "
                         "late/amended filings the date-bounded modes miss")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.period % 2 != 0:
        log.warning(f"Period {args.period} is odd → using {args.period + 1}")
        args.period += 1

    run(
        committee_id=args.committee_id,
        period=args.period,
        rpm=args.rpm,
        per_page=args.per_page,
        min_date=args.min_date,
        max_date=args.max_date,
        refresh=args.refresh,
        full=args.full,
    )


if __name__ == "__main__":
    main()
