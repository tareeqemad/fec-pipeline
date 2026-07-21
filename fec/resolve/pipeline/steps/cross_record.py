"""Step 1: Cross-record lookup — find previous employer from our data."""

import pandas as pd

from fec.log import get_logger

from ..constants import RETIRED_VALUES, NOT_REAL_EMPLOYER, NOT_REAL_PREFIXES
from ..helpers import _prev_key

logger = get_logger(__name__)


def step_cross_record(df: pd.DataFrame, prev_cache) -> int:
    """For RETIRED donors, find their employer from other records in our CSV."""
    indiv = df[df["entity_type"] == "INDIVIDUAL"]

    emp_upper = indiv["contributor_employer"].fillna("").str.upper().str.strip()
    retired_keys = set(indiv[emp_upper.isin(RETIRED_VALUES)]["donor_key"].unique())

    latest_per_dk = indiv[indiv["donor_key"].isin(retired_keys)] \
        .sort_values("contribution_receipt_date", ascending=False) \
        .drop_duplicates("donor_key", keep="first")
    dk_to_pk = {
        row["donor_key"]: _prev_key(row["contributor_name"], row.get("contributor_state", ""))
        for _, row in latest_per_dk.iterrows()
    }

    already_pks = set()
    todo = set()
    for dk, pk in dk_to_pk.items():
        if prev_cache.get(pk) is not None:
            already_pks.add(pk)
        else:
            todo.add(dk)

    if not todo:
        logger.info(f"    Cross-record: {len(already_pks):,} already cached, 0 new")
        return 0

    real_mask = emp_upper.apply(lambda e: e not in NOT_REAL_EMPLOYER and len(e) > 1 and not any(e.startswith(p) for p in NOT_REAL_PREFIXES))
    indiv_real = indiv[real_mask].sort_values("contribution_receipt_date", ascending=False)
    real_by_dk = {dk: grp for dk, grp in indiv_real.groupby("donor_key") if dk in todo}

    found = 0
    for dk in todo:
        pk = dk_to_pk[dk]
        if prev_cache.get(pk) is not None:
            continue

        grp = real_by_dk.get(dk)
        if grp is not None and len(grp) > 0:
            latest = grp.iloc[0]
            # contributor_employer_original never survives to the saved CSV resolve reads.
            emp_name = latest["contributor_employer"]
            emp_norm = latest["contributor_employer"]
            state = latest.get("contributor_state", "")

            prev_cache.put(pk, {
                "employer": emp_name,
                "employer_normalized": emp_norm,
                "state": state,
                "method": "cross_record",
            })
            found += 1

    prev_cache.save()
    logger.info(f"    Cross-record: found {found:,} previous employers "
          f"({len(already_pks):,} already cached, {len(todo)-found:,} no other record)")
    return found
