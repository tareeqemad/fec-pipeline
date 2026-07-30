"""Step 1: cross-record lookup - find previous employer from our own data."""

import pandas as pd

from fec.log import get_logger

from fec.config.constants import NOT_REAL_EMPLOYER, NOT_REAL_PREFIXES
from ..constants import RETIRED
from ..helpers import _prev_key

logger = get_logger(__name__)


def step_cross_record(df: pd.DataFrame, prev_cache) -> int:
    """For RETIRED donors, find their employer from other records in our CSV."""
    individuals = df[df["entity_type"] == "INDIVIDUAL"]

    retired_keys = set(individuals[individuals["contributor_employer"] == RETIRED]["donor_key"].unique())

    latest_per_donor = individuals[individuals["donor_key"].isin(retired_keys)] \
        .sort_values("contribution_receipt_date", ascending=False) \
        .drop_duplicates("donor_key", keep="first")
    donor_key_to_prev_key = {
        row["donor_key"]: _prev_key(row["contributor_name"], row.get("contributor_state", ""))
        for _, row in latest_per_donor.iterrows()
    }

    already_cached_keys = set()
    todo = set()
    for donor_key, prev_key in donor_key_to_prev_key.items():
        if prev_cache.get(prev_key) is not None:
            already_cached_keys.add(prev_key)
        else:
            todo.add(donor_key)

    if not todo:
        logger.info(f"    Cross-record: {len(already_cached_keys):,} already cached, 0 new")
        return 0

    emp_upper = individuals["contributor_employer"].fillna("").str.upper().str.strip()
    real_mask = emp_upper.apply(lambda employer: employer not in NOT_REAL_EMPLOYER and len(employer) > 1 and not any(employer.startswith(prefix) for prefix in NOT_REAL_PREFIXES))
    individuals_real = individuals[real_mask].sort_values("contribution_receipt_date", ascending=False)
    real_by_donor_key = {donor_key: group for donor_key, group in individuals_real.groupby("donor_key") if donor_key in todo}

    found = 0
    for donor_key in todo:
        prev_key = donor_key_to_prev_key[donor_key]
        if prev_cache.get(prev_key) is not None:
            continue

        group = real_by_donor_key.get(donor_key)
        if group is not None and len(group) > 0:
            latest = group.iloc[0]
            # contributor_employer_original never survives to the saved CSV resolve reads.
            emp = latest["contributor_employer"]
            state = latest.get("contributor_state", "")

            prev_cache.put(prev_key, {
                "employer": emp,
                "employer_normalized": emp,
                "state": state,
                "method": "cross_record",
            })
            found += 1

    prev_cache.save()
    logger.info(f"    Cross-record: found {found:,} previous employers "
          f"({len(already_cached_keys):,} already cached, {len(todo)-found:,} no other record)")
    return found
