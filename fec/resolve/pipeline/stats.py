"""Resolution statistics display."""

import pandas as pd

from fec.log import get_logger

from .constants import TIERS, NOT_REAL_EMPLOYER, RETIRED_VALUES, SELF_EMPLOYED_VALUES
from .helpers import _s, _prev_key

logger = get_logger(__name__)


def show_stats(df: pd.DataFrame, prev_cache, addr_cache, comm_cache,
               donor_totals: pd.Series) -> None:
    """Show resolution status."""

    indiv = df[df["entity_type"] == "INDIVIDUAL"]
    latest = indiv.sort_values("contribution_receipt_date").drop_duplicates("donor_key", keep="last")
    latest = latest.merge(donor_totals[["donor_key", "donor_total", "tier"]], on="donor_key", how="left")

    logger.info(f"\n  \u2500\u2500 Cache Sizes \u2500\u2500")
    logger.info(f"    Previous employer:  {len(prev_cache):>7,}")
    logger.info(f"    Employer addresses: {len(addr_cache):>7,}")
    logger.info(f"    Committee:          {len(comm_cache):>7,}")

    logger.info(f"\n  \u2500\u2500 Resolution by Tier \u2500\u2500")
    logger.info(f"  {'Tier':15s} {'Donors':>7s} {'HasEmp':>7s} {'Retired':>7s} {'SelfEmp':>7s} {'Skip':>7s} {'AddrOK':>7s}")
    logger.info(f"  {'\u2500'*15} {'\u2500'*7} {'\u2500'*7} {'\u2500'*7} {'\u2500'*7} {'\u2500'*7} {'\u2500'*7}")

    for num, label, _, _ in TIERS:
        tier = latest[latest["tier"] == num]
        n = len(tier)
        if n == 0:
            continue

        emp_upper = tier["contributor_employer"].fillna("").str.upper().str.strip()
        has_emp = (emp_upper.apply(lambda e: e not in NOT_REAL_EMPLOYER and len(e) > 1)).sum()
        retired = emp_upper.isin(RETIRED_VALUES).sum()
        self_emp = emp_upper.isin(SELF_EMPLOYED_VALUES).sum()
        skip = n - has_emp - retired - self_emp

        addr_ok = 0
        for _, row in tier.iterrows():
            emp = _s(row.get("contributor_employer")).strip().upper()
            emp_norm = emp
            state = _s(row.get("contributor_state"))

            if emp not in NOT_REAL_EMPLOYER and len(emp) > 1:
                # Cache is keyed by EMPLOYER (corporate HQ, no state)
                c = addr_cache.get(emp_norm)
                if c and c.get("employer_address"):
                    addr_ok += 1
            elif emp in RETIRED_VALUES:
                pk = _prev_key(row.get("contributor_name", ""), state)
                prev = prev_cache.get(pk)
                if prev and prev.get("employer"):
                    prev_norm = _s(prev.get("employer_normalized", prev["employer"])).strip().upper()
                    c = addr_cache.get(prev_norm)
                    if c and c.get("employer_address"):
                        addr_ok += 1
            elif emp in SELF_EMPLOYED_VALUES:
                addr_ok += 1

        logger.info(f"  {label:15s} {n:>7,} {has_emp:>7,} {retired:>7,} {self_emp:>7,} {skip:>7,} {addr_ok:>7,}")
