"""Shared helper functions for the resolve pipeline."""

import os
from pathlib import Path

import pandas as pd

from .constants import TIERS


def _s(val, default: str = "") -> str:
    """Safely convert a value to string, handling pd.NA/NaN/None."""
    if val is None or pd.isna(val):
        return default
    return str(val)


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


def _prev_key(name, state) -> str:
    """Build stable cache key for previous-employer lookup: NAME|STATE."""
    return f"{_s(name).strip()}|{_s(state).strip()}"


def _compute_donor_totals(df: pd.DataFrame) -> pd.Series:
    """Compute total per donor_key and assign tier number."""
    totals = df.groupby("donor_key")["contribution_receipt_amount"].sum().reset_index()
    totals.columns = ["donor_key", "donor_total"]

    def _tier(amount):
        for tier_number, _, low, high in TIERS:
            if low <= amount < high:
                return tier_number
        return 6

    totals["tier"] = totals["donor_total"].apply(_tier)
    return totals
