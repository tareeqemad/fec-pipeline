"""Shared helper functions for the resolve pipeline."""

import pandas as pd

from fec.cleaning.previous_employer import normalize_previous_employer_value
from fec.cleaning.employer_status import is_real_employer

from .constants import TIERS


# safely convert a value to string, handling NA/None
def _s(val, default: str = "") -> str:
    """Safely convert a value to string, handling pd.NA/NaN/None."""
    if val is None or pd.isna(val):
        return default
    return str(val)


# build a previous-employer cache key for one donor
def _prev_key(donor_key) -> str:
    """Build a previous-employer key for one donor identity."""
    return f"donor:{_s(donor_key).strip()}"


# clean previous-employer name and compatible cache keys
def _previous_employer_identity(entry: dict | None) -> tuple[str, tuple[str, ...]]:
    """Return the clean company name and compatible address-cache keys.

    The clean name is the only value written to pipeline output. Older resolve
    caches may use an unclean spelling, so those spellings remain fallback keys
    for address lookup instead of causing a duplicate AI request.
    """
    if not entry:
        return "", ()

    raw_name = _s(entry.get("employer")).strip()
    normalized_name = _s(entry.get("employer_normalized")).strip()
    source_name = _s(entry.get("employer_source")).strip()

    clean_name = ""
    for candidate in (raw_name, normalized_name, source_name):
        cleaned = normalize_previous_employer_value(candidate)
        if cleaned == "SELF-EMPLOYED" or is_real_employer(cleaned):
            clean_name = cleaned
            break
    if not clean_name:
        return "", ()

    keys = []
    for name in (clean_name, normalized_name, raw_name, source_name):
        key = name.strip().upper()
        if key and key not in keys:
            keys.append(key)
    return clean_name, tuple(keys)


# compute each donor's total amount and contribution tier
def _compute_donor_totals(df: pd.DataFrame) -> pd.Series:
    """Compute total per donor_key and assign tier number."""
    totals = df.groupby("donor_key")["contribution_receipt_amount"].sum().reset_index()
    totals.columns = ["donor_key", "donor_total"]

    # map a donation amount to its tier number
    def _tier(amount):
        for tier_number, _, low, high in TIERS:
            if low <= amount < high:
                return tier_number
        return 6

    totals["tier"] = totals["donor_total"].apply(_tier)
    return totals
