"""Step 4: committee addresses taken straight from each committee's own FEC filings - a committee IS the contributor, so its contributions already record its address."""
import pandas as pd

from fec.log import get_logger

from ..helpers import _s

logger = get_logger(__name__)


def step_committees_own_address(df: pd.DataFrame, comm_cache) -> int:
    """Fill comm_cache from each committee's most recent filing, keyed NAME|STATE (the exact key _resolve_row looks up); returns the number set."""
    committees = df[df["entity_type"] == "COMMITTEE/PAC"]
    if committees.empty:
        logger.info("    no committee rows - skipping")
        return 0

    # Sort by receipt date so each committee's latest filing sorts last.
    receipt = pd.to_datetime(committees["contribution_receipt_date"], errors="coerce")
    committees = committees.assign(_receipt=receipt).sort_values("_receipt", na_position="first")

    n_set = n_kept = 0
    for (name, state), group in committees.groupby(
            ["contributor_name", "contributor_state"], dropna=False, sort=False):
        key = f"{str(name)}|{_s(state).strip()}"
        existing = comm_cache.get(key)
        if existing and existing.get("method") == "manual_override":
            n_kept += 1
            continue  # hand-curated override wins over the filing
        latest = group.iloc[-1]
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
