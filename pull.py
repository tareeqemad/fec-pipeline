#!/usr/bin/env python3
"""Pull raw FEC contributions into data/raw/contributions.csv."""
from __future__ import annotations

import argparse
import re
from datetime import date

from fec.committees import committee_id_to_name
from fec.log import get_logger
from fec.fec_api import PullError
from fec.pull import run as pull_run

logger = get_logger(__name__)

# Use the even year ending the two-year FEC period.
def _current_period(year: int) -> int:
    return year + (year % 2)


CURRENT_PERIOD = _current_period(date.today().year)


# Validate the committee and period, then pull new records.
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check a committee's full FEC period and save new records.")
    ap.add_argument("committee_id", help="Committee ID from committees.csv.")
    ap.add_argument("--period", type=int, default=CURRENT_PERIOD,
                    help="Two-year transaction period (even year, default %(default)s).")
    ap.add_argument("--full", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    from fec import env
    env.load_env()

    committee_id = args.committee_id.strip().upper()
    if not re.fullmatch(r"C\d{8}", committee_id):
        ap.error("invalid committee id (expected C + 8 digits)")
    if committee_id not in committee_id_to_name():
        ap.error(f"{committee_id} is not configured in data/reference/committees.csv")

    period = _current_period(args.period)
    logger.info("=" * 60)
    logger.info(
        "  FEC pull - %s, period %s, full period, new records only",
        committee_id,
        period,
    )
    logger.info("=" * 60)

    try:
        pull_run(committee_id=committee_id, period=period)
    except PullError as error:
        logger.error("  %s", error)
        return 2
    except Exception as error:
        logger.error("  %s failed: %s: %s", committee_id, type(error).__name__, error)
        return 1

    logger.info("\n" + "=" * 60)
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
