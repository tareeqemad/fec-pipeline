#!/usr/bin/env python3
"""pull.py — pull FEC Schedule A data into DATA/contributions.csv.

    python pull.py                         # the 3 tracked committees (current cycle)
    python pull.py C00401224               # one specific committee
    python pull.py C00401224 C00777077     # several
    python pull.py --period 2024 C00401224 # a different two-year cycle
    python pull.py --full                  # re-pull everything (not just new records)

Thin wrapper around fec_pull.run() so the whole "fetch new data" step is a SINGLE
command. Each committee is pulled in turn;
by default only NEW records are fetched (refresh). Run clean.py afterwards.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date

from fec.log import get_logger
from fec_pull import run as pull_run

logger = get_logger(__name__)

# The three committees the project tracks (AIPAC, DMFI, UDP).
TRACKED = ["C00797670", "C00710848", "C00799031"]
_COMMITTEE_RE = re.compile(r"^C\d{8}$")     # FEC committee id = C + 8 digits


def _current_period(year: int) -> int:
    """The FEC two-year transaction period for a calendar year — its even upper
    year (2025/2026 -> 2026, 2027/2028 -> 2028). Derived from the date so the
    default cycle never silently rots at a cycle boundary (a hardcoded year
    would make every pull after Jan 1 of the next odd year fetch 0 new rows)."""
    return year + (year % 2)


# Current FEC two-year cycle, computed at import — no hardcoded year to bump.
CURRENT_PERIOD = _current_period(date.today().year)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Pull FEC data for one or more committees.")
    ap.add_argument("committee_ids", nargs="*",
                    help="Committee IDs (C + 8 digits). Default: the 3 tracked committees.")
    ap.add_argument("--period", type=int, default=CURRENT_PERIOD,
                    help="Two-year transaction period (even year, default %(default)s).")
    ap.add_argument("--rpm", type=int, default=int(os.getenv("FEC_RPM", "15")))
    ap.add_argument("--full", action="store_true",
                    help="Pull everything, not just records newer than what we have.")
    args = ap.parse_args(argv)

    ids = [c.strip().upper() for c in (args.committee_ids or TRACKED) if c.strip()]
    bad = [c for c in ids if not _COMMITTEE_RE.match(c)]
    if bad:
        ap.error(f"invalid committee id(s): {', '.join(bad)} (expected C + 8 digits)")

    period = args.period + (args.period % 2)   # FEC two-year period must be even
    refresh = not args.full

    logger.info("=" * 60)
    logger.info(f"  FEC pull — {len(ids)} committee(s), period {period}, "
                f"{'NEW records only' if refresh else 'FULL'}")
    logger.info(f"  {', '.join(ids)}")
    logger.info("=" * 60)

    failed = []
    for i, cid in enumerate(ids, 1):
        logger.info(f"\n--- [{i}/{len(ids)}] {cid} ---")
        try:
            pull_run(committee_id=cid, period=period, rpm=args.rpm,
                     per_page=100, min_date=None, max_date=None,
                     refresh=refresh, full=args.full)
        except Exception as e:  # one committee failing shouldn't abort the rest
            logger.error(f"  {cid} failed: {type(e).__name__}: {e}")
            failed.append(cid)

    logger.info("\n" + "=" * 60)
    if failed:
        logger.info(f"  Done with errors — failed: {', '.join(failed)}")
        return 1
    logger.info("  Done. Run clean.py next to process the new data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
