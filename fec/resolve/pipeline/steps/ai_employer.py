"""Step 3: web-search-grounded AI lookup of employer HQ addresses (one search per company, cached forever)."""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from fec.log import get_logger

from ..constants import RETIRED, AI_SYSTEM_PROMPT
from ..ai_client import get_ai_client, ai_method, resolver_id, ai_web_search_call
from fec.cleaning.previous_employer import is_real_employer
from ..helpers import _s, _prev_key

logger = get_logger(__name__)

# Web search runs one /responses call per entity, so fan out concurrently.
WEB_SEARCH_WORKERS = 5


def step_ai_lookup(df: pd.DataFrame, prev_cache, addr_cache,
                   donor_totals: pd.Series, active_tiers: list,
                   dry_run: bool = False) -> int:
    """Look up employer addresses via the AI provider (batch)."""
    try:
        client, model, provider = get_ai_client()
    except ImportError:
        logger.info("    openai package not installed - run: pip install openai")
        return 0
    if client is None:
        return 0

    resolver_tag = resolver_id()

    individuals = df[df["entity_type"] == "INDIVIDUAL"]

    tier_keys = set(donor_totals[donor_totals["tier"].isin(active_tiers)]["donor_key"])
    tier_individuals = individuals[individuals["donor_key"].isin(tier_keys)]

    def _needs_ai(key):
        cached = addr_cache.get(key)
        if cached is None:
            return True
        method = cached.get("method", "")
        if method in ("ai_error", "fec_po_box", "fec_not_found", "needs_branch_lookup"):
            return True
        # A not-found from a different resolver retries once; legacy entries lack a provider tag.
        if method == "ai_not_found" and cached.get("provider") != resolver_tag:
            return True
        return False

    lookups = []
    seen = set()

    # Path 1: distinct real employers. Cache keyed by EMPLOYER alone - one HQ per company.
    real_mask = tier_individuals["contributor_employer"].map(lambda value: is_real_employer(_s(value).strip()))
    real_emps = (tier_individuals.loc[real_mask, "contributor_employer"]
                 .map(lambda value: _s(value).strip())
                 .replace("", pd.NA).dropna().drop_duplicates())
    for emp in real_emps:
        key = emp.upper()
        if _needs_ai(key) and key not in seen:
            lookups.append((emp, emp))
            seen.add(key)

    # Path 2: RETIRED donors - feed the previous employer name into the HQ lookup.
    retired_mask = tier_individuals["contributor_employer"] == RETIRED
    for _, row in tier_individuals.loc[retired_mask].drop_duplicates("donor_key").iterrows():
        state = _s(row.get("contributor_state")).strip()
        prev_key = _prev_key(row["contributor_name"], state)
        prev_entry = prev_cache.get(prev_key)
        if prev_entry and prev_entry.get("employer"):
            prev_normalized = _s(prev_entry.get("employer_normalized", prev_entry["employer"])).strip()
            if not prev_normalized:
                continue
            key = prev_normalized.upper()
            if _needs_ai(key) and key not in seen:
                lookups.append((prev_normalized, prev_entry["employer"]))
                seen.add(key)

    logger.info(f"    AI Lookup: {len(lookups):,} employers to resolve "
                f"({len(addr_cache):,} already cached)")

    if dry_run or not lookups:
        if dry_run and lookups:
            logger.info(f"    (dry run - ~{len(lookups)} web-search calls)")
        return 0

    lookups = sorted(lookups, key=lambda pair: str(pair[0]))

    # One web-search /responses call per employer - grounded answers only,
    # so every new cache entry lands in the trusted ai_*_search tier.
    def _emp_prompt(item):
        emp_norm, _ = item
        return (f'Find the US corporate headquarters address for this '
                f'employer — the canonical HQ, not a branch:\n\n'
                f'1. "{emp_norm}"')

    def _emp_store(item, obj):
        key = item[0].upper()
        if obj and obj.get("confidence", "UNKNOWN") != "UNKNOWN" and obj.get("address"):
            addr_cache.put(key, {
                "employer_address": obj.get("address", ""),
                "employer_city": obj.get("city", ""),
                "employer_state": obj.get("state", ""),
                "employer_zip": obj.get("zip", ""),
                "method": ai_method(provider),
                "confidence": obj.get("confidence", "MEDIUM")})
            return True
        addr_cache.put(key, {"employer_address": "", "method": "ai_not_found",
                             "provider": resolver_tag, "confidence": "UNKNOWN"})
        return False

    return run_web_search(client, model, AI_SYSTEM_PROMPT, lookups,
                          _emp_prompt, _emp_store, addr_cache, "AI Lookup")


def _parse_ai_json(text: str) -> list | None:
    """Robustly parse JSON from AI response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        result = json.loads(text)
        if isinstance(result, dict):
            for value in result.values():
                if isinstance(value, list):
                    return value
            return [result]
        return result
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass

    results = []
    for line in text.split("\n"):
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if results:
        return results

    raise json.JSONDecodeError("Could not parse AI response", text, 0)


def run_web_search(client, model, system_prompt, items, build_prompt,
                   store_fn, cache, label) -> int:
    """One web-search /responses call per item, concurrently; store_fn(item, obj_or_None) writes the cache entry and returns True when a real address was stored. Returns the resolved count."""
    found = 0
    cost = 0.0
    total = len(items)

    def _one(item):
        text, call_cost = ai_web_search_call(client, model, system_prompt,
                                             build_prompt(item))
        return item, text, call_cost

    with ThreadPoolExecutor(max_workers=WEB_SEARCH_WORKERS) as pool:
        futures = [pool.submit(_one, item) for item in items]
        for done, future in enumerate(as_completed(futures), 1):
            try:
                item, text, call_cost = future.result()
                cost += call_cost
                try:
                    parsed = _parse_ai_json(text)
                except json.JSONDecodeError:
                    parsed = []
                obj = next((entry for entry in parsed if isinstance(entry, dict)), None)
            except Exception as error:
                logger.error(f"      {label} web-search error - {error}")
                continue
            if store_fn(item, obj):
                found += 1
            if done % 25 == 0:
                cache.save()
                logger.info(f"      {done}/{total} - {found} found - ~${cost:.2f}")

    cache.save()
    logger.info(f"    {label} (web search): found {found:,}/{total:,} "
                f"- cost ~${cost:.2f}")
    return found
