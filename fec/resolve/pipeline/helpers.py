"""Shared helper functions for the resolve pipeline."""

import os
from pathlib import Path

import pandas as pd

from .constants import TIERS, NOT_REAL_EMPLOYER


def _s(val, default: str = "") -> str:
    """Safely convert a value to string, handling pd.NA/NaN/None."""
    if val is None or pd.isna(val):
        return default
    return str(val)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv; load_dotenv()
    except ImportError:
        env = Path(__file__).resolve().parent.parent.parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def _is_real_employer(emp: str) -> bool:
    """Return True if employer is a real company name (not RETIRED, SELF-EMPLOYED, etc.)."""
    if not emp or pd.isna(emp):
        return False
    s = str(emp).strip()
    if s.lower() in ('nan', 'none', 'n/a', 'na', ''):
        return False
    return s.upper() not in NOT_REAL_EMPLOYER


def _prev_key(name, state) -> str:
    """Build stable cache key for previous-employer lookup: NAME|STATE."""
    return f"{_s(name).strip()}|{_s(state).strip()}"


def _compute_donor_totals(df: pd.DataFrame) -> pd.Series:
    """Compute total per donor_key and assign tier number."""
    totals = df.groupby("donor_key")["contribution_receipt_amount"].sum().reset_index()
    totals.columns = ["donor_key", "donor_total"]

    def _tier(amount):
        for num, _, lo, hi in TIERS:
            if lo <= amount < hi:
                return num
        return 6

    totals["tier"] = totals["donor_total"].apply(_tier)
    return totals
