"""Sync leaders.csv and key_accomplices.csv with the newest cleaned FEC filing per donor.

    python sync_rosters.py           # rewrite the FEC-linked roster rows
    python sync_rosters.py --check   # report drift only; exit 1 if any

Run it after clean.py / resolve.py / geocode.py and before loader.py.
"""
from __future__ import annotations

import argparse
import sys

from fec.database.roster_sync import report, sync_rosters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="report drift without writing; exit 1 if any")
    args = parser.parse_args()
    return report(sync_rosters(check=args.check), args.check)


if __name__ == "__main__":
    sys.exit(main())
