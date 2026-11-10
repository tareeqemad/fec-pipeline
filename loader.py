#!/usr/bin/env python3
"""Load the cleaned data into PostgreSQL.

Run: python loader.py --reset
"""

from fec.database.pipeline import main

if __name__ == "__main__":
    main()
