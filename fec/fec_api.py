"""HTTP access to the FEC API: session, retries, rate limit, one page."""
from __future__ import annotations

import logging
import os
from time import monotonic, sleep

import requests
from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout

log = logging.getLogger(__name__)

BASE_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
MAX_ATTEMPTS = 6
RATE_LIMIT_MAX_WAIT = 65 * 60
TRANSIENT_STATUSES = {500, 502, 503, 504}


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
