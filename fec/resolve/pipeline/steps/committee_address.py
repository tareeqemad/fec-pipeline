"""Step 4: Committee addresses — taken straight from the committees' own
FEC filings.

A committee IS the contributor, so every contribution it makes already
records its address (street/city/state/zip — 100% populated in FEC data).
No lookup is needed: we use the address from each committee's MOST RECENT
contribution. Hand-curated exceptions in manual_committee_addresses.csv
(method='manual_override', loaded in Step 0) are left untouched.
"""
import pandas as pd

from fec.log import get_logger

from ..helpers import _s

logger = get_logger(__name__)


def step_committees_own_address(df: pd.DataFrame, comm_cache) -> int:
    """Populate comm_cache with each committee's address from its latest
    filing. Keyed by NAME|STATE — the exact key _resolve_row() looks up.
    Returns the number of committees set."""
    comm = df[df["entity_type"] == "COMMITTEE/PAC"]
    if comm.empty:
        logger.info("    no committee rows — skipping")
        return 0

    # Sort by receipt date so each committee's latest filing sorts last.
    receipt = pd.to_datetime(comm["contribution_receipt_date"], errors="coerce")
    comm = comm.assign(_receipt=receipt).sort_values("_receipt", na_position="first")

    n_set = n_kept = 0
    for (name, state), grp in comm.groupby(
            ["contributor_name", "contributor_state"], dropna=False, sort=False):
        key = f"{str(name)}|{_s(state).strip()}"
        existing = comm_cache.get(key)
        if existing and existing.get("method") == "manual_override":
            n_kept += 1
            continue  # hand-curated override wins over the filing
        latest = grp.iloc[-1]
        street = _s(latest.get("contributor_street_1")).strip()
        if not street:
            continue
        comm_cache.put(key, {
            "employer_address": street,
            "employer_city":  _s(latest.get("contributor_city")).strip(),
            "employer_state": _s(latest.get("contributor_state")).strip(),
            "employer_zip":   _s(latest.get("contributor_zip")).strip(),
            "method":         "committee_own_address",
            "confidence":     "HIGH",
        })
        n_set += 1

    comm_cache.save()
    logger.info(f"    Committee addresses: {n_set:,} set from latest filing"
                + (f", {n_kept:,} kept from manual overrides" if n_kept else ""))
    return n_set
