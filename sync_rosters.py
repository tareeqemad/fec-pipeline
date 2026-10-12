"""Sync roster CSVs to newest FEC filings."""
from __future__ import annotations

import argparse
import sys

from fec.database.roster_sync import report, sync_rosters


# parse args, then sync or check rosters
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift without writing; exit 1 if any")
    args = parser.parse_args()
    return report(sync_rosters(check=args.check), args.check)


if __name__ == "__main__":
    sys.exit(main())
