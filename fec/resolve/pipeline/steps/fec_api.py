"""Step 2: FEC API — find previous employer from other committees."""

import os
import time

import pandas as pd

from fec.log import get_logger

from ..constants import RETIRED_VALUES, NOT_REAL_EMPLOYER, NOT_REAL_PREFIXES
from ..helpers import _prev_key

logger = get_logger(__name__)


def step_fec_api(df: pd.DataFrame, prev_cache, donor_totals: pd.Series,
                 active_tiers: list, dry_run: bool = False) -> int:
    """For RETIRED donors without cross-record match, search FEC API (parallel)."""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    fec_key = os.environ.get("FEC_API_KEY", "").strip()
    if not fec_key:
        logger.info("    \u26a0 FEC_API_KEY not found in .env \u2014 skipping")
        logger.info("    Get a free key at: https://api.open.fec.gov/developers/")
        return 0

    FEC_BASE = "https://api.open.fec.gov/v1"

    indiv = df[df["entity_type"] == "INDIVIDUAL"]
    emp_upper = indiv["contributor_employer"].fillna("").str.upper().str.strip()
    retired_keys = set(indiv[emp_upper.isin(RETIRED_VALUES)]["donor_key"].unique())

    tier_keys = set(donor_totals[donor_totals["tier"].isin(active_tiers)]["donor_key"])

    latest_retired = indiv[indiv["donor_key"].isin(retired_keys)] \
        .sort_values("contribution_receipt_date", ascending=False) \
        .drop_duplicates("donor_key", keep="first")
    dk_info = {}
    for _, row in latest_retired.iterrows():
        dk = row["donor_key"]
        pk = _prev_key(row["contributor_name"], row.get("contributor_state", ""))
        dk_info[dk] = {
            "prev_key": pk,
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
        }

    todo_pks = set()
    todo_keys = set()
    for dk, info in dk_info.items():
        pk = info["prev_key"]
        if dk in tier_keys and pk not in todo_pks and prev_cache.get(pk) is None:
            todo_keys.add(dk)
            todo_pks.add(pk)

    if not todo_keys:
        logger.info(f"    FEC API: 0 retired donors to search")
        return 0

    totals_map = donor_totals.set_index("donor_key")["donor_total"].to_dict()
    search_list = []
    for dk in todo_keys:
        info = dk_info[dk]
        info["total"] = totals_map.get(dk, 0)
        search_list.append(info)

    search_list.sort(key=lambda x: -x["total"])

    logger.info(f"    FEC API: {len(search_list):,} retired donors to search")

    if dry_run:
        logger.info(f"    (dry run \u2014 would make {len(search_list):,} API calls)")
        return 0

    def _lookup_one(person):
        """Single FEC API lookup for one retired donor."""
        try:
            resp = requests.get(f"{FEC_BASE}/schedules/schedule_a/", params={
                "api_key": fec_key,
                "contributor_name": person["name"],
                "contributor_state": person["state"],
                "per_page": 20,
                "sort": "-contribution_receipt_amount",
                "is_individual": "true",
            }, timeout=15)

            if resp.status_code == 429:
                time.sleep(5)
                return None, None

            if resp.status_code == 200:
                for r in resp.json().get("results", []):
                    raw_emp = (r.get("contributor_employer") or "").strip().upper()
                    is_real = raw_emp and raw_emp not in NOT_REAL_EMPLOYER and len(raw_emp) > 2
                    if is_real and any(raw_emp.startswith(p) for p in NOT_REAL_PREFIXES):
                        is_real = False
                    if is_real:
                        return person["prev_key"], {
                            "employer": r["contributor_employer"].strip(),
                            "employer_normalized": raw_emp,
                            "state": r.get("contributor_state", person["state"]),
                            "method": "fec_api",
                        }
                return person["prev_key"], {
                    "employer": "",
                    "method": "fec_api_not_found",
                }

            return None, None

        except requests.exceptions.Timeout:
            return None, None
        except Exception:
            return None, None

    found = 0
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_lookup_one, p): p for p in search_list}
        done = 0
        for future in as_completed(futures):
            pk, result = future.result()
            done += 1
            if pk and result:
                prev_cache.put(pk, result)
                if result.get("employer"):
                    found += 1

            if done % 50 == 0:
                prev_cache.save()
                logger.info(f"      ... {done}/{len(search_list)} ({found:,} found)")

    prev_cache.save()
    logger.info(f"    FEC API: found {found:,} previous employers")
    return found
