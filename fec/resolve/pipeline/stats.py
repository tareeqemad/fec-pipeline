"""Resolution statistics display."""

import pandas as pd

from fec.log import get_logger

from .constants import TIERS

logger = get_logger(__name__)


def show_stats(df: pd.DataFrame, prev_cache, addr_cache, comm_cache,
               donor_totals: pd.Series) -> None:
    """Show resolution status."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    latest = (individuals.sort_values("contribution_receipt_date")
              .drop_duplicates("donor_key", keep="last"))
    latest = latest.merge(
        donor_totals[["donor_key", "donor_total", "tier"]],
        on="donor_key", how="left",
    )

    logger.info("\n  -- Cache Sizes --")
    logger.info(f"    Previous employer:  {len(prev_cache):>7,}")
    logger.info(f"    Employer addresses: {len(addr_cache):>7,}")
    logger.info(f"    Committee:          {len(comm_cache):>7,}")

    logger.info("\n  -- Resolution by Tier --")
    logger.info(
        f"  {'Tier':15s} {'Donors':>7s} {'HasEmp':>7s} {'Retired':>7s} "
        f"{'SelfEmp':>7s} {'Skip':>7s} {'AddrOK':>7s}"
    )
    logger.info(
        f"  {'-' * 15} {'-' * 7} {'-' * 7} {'-' * 7} "
        f"{'-' * 7} {'-' * 7} {'-' * 7}"
    )

    for tier_number, label, _, _ in TIERS:
        tier = latest[latest["tier"] == tier_number]
        n_donors = len(tier)
        if n_donors == 0:
            continue

        status = tier["employer_status"]
        has_emp = status.eq("active").sum()
        retired = status.eq("retired").sum()
        self_emp = status.eq("self_employed").sum()
        skip = n_donors - has_emp - retired - self_emp
        addr_ok = tier["employer_address"].fillna("").str.strip().ne("").sum()

        logger.info(
            f"  {label:15s} {n_donors:>7,} {has_emp:>7,} "
            f"{retired:>7,} {self_emp:>7,} {skip:>7,} {addr_ok:>7,}"
        )
