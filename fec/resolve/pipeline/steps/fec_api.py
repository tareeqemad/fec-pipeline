"""Step 2: FEC API - find previous employer from other committees."""

import os
import time

import pandas as pd

from fec.log import get_logger

from fec.config.constants import NOT_REAL_EMPLOYER, NOT_REAL_PREFIXES
from ..constants import RETIRED
from ..helpers import _prev_key

logger = get_logger(__name__)

FEC_BASE = "https://api.open.fec.gov/v1"


def step_fec_api(df: pd.DataFrame, prev_cache, donor_totals: pd.Series,
                 active_tiers: list, dry_run: bool = False) -> int:
    """For RETIRED donors without cross-record match, search FEC API (parallel)."""
    import requests
    from concurrent.futures import ThreadPoolExecutor, as_completed

    fec_key = os.environ.get("FEC_API_KEY", "").strip()
    if not fec_key:
        logger.info("    FEC_API_KEY not found in .env - skipping")
        logger.info("    Get a free key at: https://api.open.fec.gov/developers/")
        return 0

    individuals = df[df["entity_type"] == "INDIVIDUAL"]
    retired_keys = set(individuals[individuals["contributor_employer"] == RETIRED]["donor_key"].unique())

    tier_keys = set(donor_totals[donor_totals["tier"].isin(active_tiers)]["donor_key"])

    latest_retired = individuals[individuals["donor_key"].isin(retired_keys)] \
        .sort_values("contribution_receipt_date", ascending=False) \
        .drop_duplicates("donor_key", keep="first")
    donor_info = {}
    for _, row in latest_retired.iterrows():
        donor_key = row["donor_key"]
        prev_key = _prev_key(row["contributor_name"], row.get("contributor_state", ""))
        donor_info[donor_key] = {
            "prev_key": prev_key,
            "name": row["contributor_name"],
            "state": row.get("contributor_state", ""),
        }

    todo_prev_keys = set()
    todo_donor_keys = set()
    for donor_key, info in donor_info.items():
        prev_key = info["prev_key"]
        if donor_key in tier_keys and prev_key not in todo_prev_keys and prev_cache.get(prev_key) is None:
            todo_donor_keys.add(donor_key)
            todo_prev_keys.add(prev_key)

    if not todo_donor_keys:
        logger.info("    FEC API: 0 retired donors to search")
        return 0

    totals_map = donor_totals.set_index("donor_key")["donor_total"].to_dict()
    search_list = []
    for donor_key in todo_donor_keys:
        info = donor_info[donor_key]
        info["total"] = totals_map.get(donor_key, 0)
        search_list.append(info)

    search_list.sort(key=lambda entry: -entry["total"])

    logger.info(f"    FEC API: {len(search_list):,} retired donors to search")

    if dry_run:
        logger.info(f"    (dry run - would make {len(search_list):,} API calls)")
        return 0

    def _lookup_one(person):
        """Single FEC API lookup for one retired donor."""
        try:
            response = requests.get(f"{FEC_BASE}/schedules/schedule_a/", params={
                "api_key": fec_key,
                "contributor_name": person["name"],
                "contributor_state": person["state"],
                "per_page": 20,
                "sort": "-contribution_receipt_amount",
                "is_individual": "true",
            }, timeout=15)

            if response.status_code == 429:
                time.sleep(5)
                return None, None

            if response.status_code == 200:
                for record in response.json().get("results", []):
                    raw_emp = (record.get("contributor_employer") or "").strip().upper()
                    is_real = raw_emp and raw_emp not in NOT_REAL_EMPLOYER and len(raw_emp) > 2
                    if is_real and any(raw_emp.startswith(prefix) for prefix in NOT_REAL_PREFIXES):
                        is_real = False
                    if is_real:
                        return person["prev_key"], {
                            "employer": record["contributor_employer"].strip(),
                            "employer_normalized": raw_emp,
                            "state": record.get("contributor_state", person["state"]),
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
        futures = {pool.submit(_lookup_one, person): person for person in search_list}
        done = 0
        for future in as_completed(futures):
            prev_key, result = future.result()
            done += 1
            if prev_key and result:
                prev_cache.put(prev_key, result)
                if result.get("employer"):
                    found += 1

            if done % 50 == 0:
                prev_cache.save()
                logger.info(f"      ... {done}/{len(search_list)} ({found:,} found)")

    prev_cache.save()
    logger.info(f"    FEC API: found {found:,} previous employers")
    return found
