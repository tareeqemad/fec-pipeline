#!/usr/bin/env python3
"""Pull FEC Schedule A data for one or more committees (default: the 3 tracked) into data/contributions.csv; run clean.py afterwards."""
from __future__ import annotations

import argparse
import re
from datetime import date

from fec.log import get_logger
from fec.pull import run as pull_run

logger = get_logger(__name__)

# the three committees the project tracks (AIPAC, DMFI, UDP)
TRACKED = ["C00797670", "C00710848", "C00799031"]
_COMMITTEE_RE = re.compile(r"^C\d{8}$")     # FEC committee id = C + 8 digits


def _current_period(year: int) -> int:
    """FEC two-year period for a calendar year: its even upper year (2025/2026 -> 2026)."""
    return year + (year % 2)


# computed at import - a hardcoded year would silently rot at a cycle boundary
CURRENT_PERIOD = _current_period(date.today().year)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pull FEC data for one or more committees.")
    ap.add_argument("committee_ids", nargs="*",
                    help="Committee IDs (C + 8 digits). Default: the 3 tracked committees.")
    ap.add_argument("--period", type=int, default=CURRENT_PERIOD,
                    help="Two-year transaction period (even year, default %(default)s).")
    ap.add_argument("--full", action="store_true",
                    help="Pull everything, not just records newer than what we have.")
    args = ap.parse_args(argv)

    from fec import env
    env.load_env()   # FEC_API_KEY (and optional FEC_RPM) come from .env

    ids = [c.strip().upper() for c in (args.committee_ids or TRACKED) if c.strip()]
    bad = [c for c in ids if not _COMMITTEE_RE.match(c)]
    if bad:
        ap.error(f"invalid committee id(s): {', '.join(bad)} (expected C + 8 digits)")

    period = args.period + (args.period % 2)   # FEC two-year period must be even

    logger.info("=" * 60)
    logger.info(f"  FEC pull - {len(ids)} committee(s), period {period}, "
                f"{'FULL' if args.full else 'NEW records only'}")
    logger.info(f"  {', '.join(ids)}")
    logger.info("=" * 60)

    failed = []
    for i, cid in enumerate(ids, 1):
        logger.info(f"\n--- [{i}/{len(ids)}] {cid} ---")
        try:
            pull_run(committee_id=cid, period=period, full=args.full)
        except Exception as e:  # one committee failing shouldn't abort the rest
            logger.error(f"  {cid} failed: {type(e).__name__}: {e}")
            failed.append(cid)

    logger.info("\n" + "=" * 60)
    if failed:
        logger.info(f"  Done with errors - failed: {', '.join(failed)}")
        return 1
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
