#!/usr/bin/env python3
"""Pull raw FEC contributions into data/contributions.csv."""
from __future__ import annotations

import argparse
import re
from datetime import date

from fec.committees import committee_id_to_name
from fec.log import get_logger
from fec.pull import PullError, required_env, run as pull_run

logger = get_logger(__name__)

_COMMITTEE_RE = re.compile(r"^C\d{8}$")


def _current_period(year: int) -> int:
    """Return the FEC two-year period."""
    return year + (year % 2)


CURRENT_PERIOD = _current_period(date.today().year)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pull FEC data for one or more committees.")
    ap.add_argument("committee_ids", nargs="*",
                    help="Committee IDs. Default: all committees in committees.csv.")
    ap.add_argument("--period", type=int, default=CURRENT_PERIOD,
                    help="Two-year transaction period (even year, default %(default)s).")
    ap.add_argument("--full", action="store_true",
                    help="Pull everything, not just records newer than what we have.")
    args = ap.parse_args(argv)

    from fec import env
    env.load_env()

    tracked = list(committee_id_to_name())
    requested = args.committee_ids or tracked
    ids = [c.strip().upper() for c in requested if c.strip()]
    if not ids:
        if args.committee_ids:
            ap.error("committee ids cannot be blank")
        ap.error("no committees configured in data/database/committees.csv")
    bad = [c for c in ids if not _COMMITTEE_RE.match(c)]
    if bad:
        ap.error(f"invalid committee id(s): {', '.join(bad)} (expected C + 8 digits)")

    try:
        required_env("FEC_API_KEY")
    except PullError as error:
        logger.error("%s", error)
        return 2

    period = _current_period(args.period)

    logger.info("=" * 60)
    logger.info(
        "  FEC pull - %s committee(s), period %s, %s",
        len(ids), period, "FULL" if args.full else "NEW records only",
    )
    logger.info("  %s", ", ".join(ids))
    logger.info("=" * 60)

    failed = []
    for i, cid in enumerate(ids, 1):
        logger.info("\n--- [%s/%s] %s ---", i, len(ids), cid)
        try:
            pull_run(committee_id=cid, period=period, full=args.full)
        except Exception as error:
            logger.error(
                "  %s failed: %s: %s", cid, type(error).__name__, error,
            )
            failed.append(cid)

    logger.info("\n" + "=" * 60)
    if failed:
        logger.info("  Done with errors - failed: %s", ", ".join(failed))
        return 1
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
