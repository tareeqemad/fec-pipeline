#!/usr/bin/env python3
"""Pull raw FEC contributions into data/raw/contributions.csv."""
from __future__ import annotations

import argparse
import re
from datetime import date

from fec.committees import committee_id_to_name
from fec.log import get_logger
from fec.fec_api import PullError
from fec.pull import backfill_source
from fec.pull import run as pull_run

logger = get_logger(__name__)

_COMMITTEE_RE = re.compile(r"^C\d{8}$")


def _current_period(year: int) -> int:
    """Return the FEC two-year period."""
    return year + (year % 2)


CURRENT_PERIOD = _current_period(date.today().year)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pull FEC data for one committee.")
    ap.add_argument("committee_id", help="Committee ID from committees.csv.")
    ap.add_argument("--period", type=int, default=CURRENT_PERIOD,
                    help="Two-year transaction period (even year, default %(default)s).")
    ap.add_argument(
        "--full",
        action="store_true",
        help="Recheck the complete period instead of starting at the latest date.",
    )
    ap.add_argument(
        "--backfill-source",
        action="store_true",
        help="Fill FEC's own entity type and contributor id for rows already pulled "
             "(matched by sub_id, else one exact transaction match; never by name). Appends nothing.",
    )
    args = ap.parse_args(argv)

    from fec import env
    env.load_env()

    committee_id = args.committee_id.strip().upper()
    if not _COMMITTEE_RE.match(committee_id):
        ap.error("invalid committee id (expected C + 8 digits)")
    committees = committee_id_to_name()
    if committee_id not in committees:
        ap.error(
            f"{committee_id} is not configured in data/rules/committees.csv"
        )

    period = _current_period(args.period)
    if args.backfill_source and args.full:
        ap.error("--backfill-source and --full do different things; run them separately")

    logger.info("=" * 60)
    logger.info(
        "  FEC pull - %s, period %s, %s",
        committee_id,
        period,
        "backfill FEC source fields" if args.backfill_source else "full period" if args.full else "new records",
    )
    logger.info("=" * 60)

    try:
        if args.backfill_source:
            stats = backfill_source(committee_id, period)
            if stats["unmatched"] or stats["ambiguous"]:
                logger.warning("  %s rows left without FEC source fields (see counts above)",
                               f"{stats['unmatched'] + stats['ambiguous']:,}")
            return 0
        pull_run(
            committee_id=committee_id,
            period=period,
            full=args.full,
        )
    except PullError as error:
        logger.error("  %s", error)
        return 2
    except Exception as error:
        logger.error(
            "  %s failed: %s: %s",
            committee_id,
            type(error).__name__,
            error,
        )
        return 1

    logger.info("\n" + "=" * 60)
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
