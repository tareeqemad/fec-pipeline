"""Resolution statistics display."""

import pandas as pd

from fec.log import get_logger

from fec.config.constants import NOT_REAL_EMPLOYER
from .constants import TIERS, RETIRED, SELF_EMPLOYED
from .helpers import _s, _prev_key

logger = get_logger(__name__)


def show_stats(df: pd.DataFrame, prev_cache, addr_cache, comm_cache,
               donor_totals: pd.Series) -> None:
    """Show resolution status."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    latest = individuals.sort_values("contribution_receipt_date").drop_duplicates("donor_key", keep="last")
    latest = latest.merge(donor_totals[["donor_key", "donor_total", "tier"]], on="donor_key", how="left")

    logger.info("\n  -- Cache Sizes --")
    logger.info(f"    Previous employer:  {len(prev_cache):>7,}")
    logger.info(f"    Employer addresses: {len(addr_cache):>7,}")
    logger.info(f"    Committee:          {len(comm_cache):>7,}")

    logger.info("\n  -- Resolution by Tier --")
    logger.info(f"  {'Tier':15s} {'Donors':>7s} {'HasEmp':>7s} {'Retired':>7s} {'SelfEmp':>7s} {'Skip':>7s} {'AddrOK':>7s}")
    logger.info(f"  {'-'*15} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*7}")

    for tier_number, label, _, _ in TIERS:
        tier = latest[latest["tier"] == tier_number]
        n_donors = len(tier)
        if n_donors == 0:
            continue

        emp_upper = tier["contributor_employer"].fillna("").str.upper().str.strip()
        has_emp = (emp_upper.apply(lambda employer: employer not in NOT_REAL_EMPLOYER and len(employer) > 1)).sum()
        retired = (emp_upper == RETIRED).sum()
        self_emp = (emp_upper == SELF_EMPLOYED).sum()
        skip = n_donors - has_emp - retired - self_emp

        addr_ok = 0
        for _, row in tier.iterrows():
            emp = _s(row.get("contributor_employer")).strip().upper()
            state = _s(row.get("contributor_state"))

            if emp not in NOT_REAL_EMPLOYER and len(emp) > 1:
                # Cache is keyed by EMPLOYER (corporate HQ, no state).
                cached = addr_cache.get(emp)
                if cached and cached.get("employer_address"):
                    addr_ok += 1
            elif emp == RETIRED:
                prev_key = _prev_key(row.get("contributor_name", ""), state)
                prev_entry = prev_cache.get(prev_key)
                if prev_entry and prev_entry.get("employer"):
                    prev_normalized = _s(prev_entry.get("employer_normalized", prev_entry["employer"])).strip().upper()
                    cached = addr_cache.get(prev_normalized)
                    if cached and cached.get("employer_address"):
                        addr_ok += 1
            elif emp == SELF_EMPLOYED:
                addr_ok += 1

        logger.info(f"  {label:15s} {n_donors:>7,} {has_emp:>7,} {retired:>7,} {self_emp:>7,} {skip:>7,} {addr_ok:>7,}")
