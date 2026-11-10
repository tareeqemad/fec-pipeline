#!/usr/bin/env python3
"""Pull new FEC contributions of one committee.

Run: python pull.py C00797670 --period 2026
"""

from fec.pull.pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
