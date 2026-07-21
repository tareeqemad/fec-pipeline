"""
resolve.pipeline — Resolve employer addresses from real data sources.

Steps:
    1. Cross-record lookup (free)
    2. FEC API (free)
    3. AI Lookup via OpenAI GPT-4o-mini (~$0.37)
    4. AI Lookup for committees (~$0.10)
"""

from .cli import main

__all__ = ["main"]
