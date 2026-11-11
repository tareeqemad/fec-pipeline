"""FEC API access: the rate limit, retries, and following the pages of one query."""

from collections.abc import Iterator
from time import monotonic, sleep

import requests
from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout

from fec.common.log import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"

MAX_ATTEMPTS = 6

RATE_LIMIT_MAX_WAIT = 65 * 60

TRANSIENT_STATUSES = {500, 502, 503, 504}


class PullError(RuntimeError):
    """A pull cannot continue safely."""


class RateLimiter:
    """Keep request starts within the configured requests-per-minute limit."""

    def __init__(self, requests_per_minute: int) -> None:
        """Set the minimum delay between request starts."""
        self.interval = 60.0 / max(1, requests_per_minute)
        self.last_request = 0.0

    def wait(self) -> None:
        """Wait until the next request is allowed."""
        delay = self.last_request + self.interval - monotonic()
        if delay > 0:
            sleep(delay)
        self.last_request = monotonic()


def _retry_delay(attempt: int, response: requests.Response | None, longest: float) -> float:
    # The server's Retry-After when it sent a readable one, else 1s, 2s, 4s ... up to the longest wait.
    retry_after = response.headers.get("Retry-After") if response is not None else None
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return min(longest, 2 ** (attempt - 1))


def _wait_after_failure(attempt: int, failure: str, response: requests.Response | None) -> None:
    delay = _retry_delay(attempt, response, 60.0)
    logger.warning("%s; retrying in %.1fs", failure, delay)
    sleep(delay)


def _read_page(response: requests.Response, api_key: str) -> dict:
    if response.status_code == 403:
        raise PullError("FEC API key is invalid or expired")
    if response.status_code == 404:
        raise PullError("FEC endpoint or committee was not found")
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as error:
        # The error text holds the request URL, and the URL holds the key.
        raise PullError(str(error).replace(api_key, "***")) from error
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError as error:
        raise PullError("FEC API returned invalid JSON") from error


def fetch_page(session: requests.Session, params: dict, limiter: RateLimiter) -> dict:
    """Fetch one page, retrying temporary errors and waiting for the rate limit to clear."""
    failures = 0
    rate_limit_answers = 0
    rate_limit_waited = 0.0
    while True:
        limiter.wait()
        try:
            response = session.get(BASE_URL, params=params, timeout=(10, 180))
        except (ReadTimeout, ConnectTimeout, ConnectionError) as error:
            failures += 1
            if failures == MAX_ATTEMPTS:
                raise PullError(f"FEC API unavailable after {failures} attempts") from error
            _wait_after_failure(failures, type(error).__name__, None)
            continue

        if response.status_code == 429 or "OVER_RATE_LIMIT" in response.text:
            rate_limit_answers += 1
            delay = max(1.0, _retry_delay(rate_limit_answers, response, 120.0))
            if rate_limit_waited + delay > RATE_LIMIT_MAX_WAIT:
                raise PullError(f"FEC rate limit did not clear after {rate_limit_waited / 60:.0f} minutes")
            rate_limit_waited += delay
            logger.warning("FEC rate limit; retrying in %.1fs (waited %.1f min)", delay, rate_limit_waited / 60)
            sleep(delay)
        elif response.status_code in TRANSIENT_STATUSES:
            failures += 1
            if failures == MAX_ATTEMPTS:
                raise PullError(f"FEC API returned HTTP {response.status_code} after {failures} attempts")
            _wait_after_failure(failures, f"HTTP {response.status_code}", response)
        else:
            return _read_page(response, params["api_key"])


def iter_pages(session: requests.Session, params: dict, limiter: RateLimiter) -> Iterator[dict]:
    """Follow FEC pagination cursors until no results or cursor remain."""
    params = dict(params)
    while True:
        page = fetch_page(session, params, limiter)
        yield page

        last_indexes = (page.get("pagination") or {}).get("last_indexes")
        if not page.get("results") or not last_indexes:
            return
        params["last_contribution_receipt_date"] = last_indexes.get("last_contribution_receipt_date")
        params["last_index"] = last_indexes.get("last_index")
