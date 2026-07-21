"""Step 3: AI Lookup — employer HQ addresses via the configured AI provider."""

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd

from fec.log import get_logger

from ..constants import RETIRED_VALUES, AI_BATCH_SIZE
from ..ai_client import (get_ai_client, ai_json_call, ai_method, resolver_id,
                         ai_web_search_call)
from ..helpers import _s, _prev_key, _is_real_employer
from ..prompts import AI_SYSTEM_PROMPT

logger = get_logger(__name__)

# Web search runs one /responses call per entity, so fan out concurrently.
WEB_SEARCH_WORKERS = 5


def _parse_ai_json(text: str) -> Optional[list]:
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
            for v in result.values():
                if isinstance(v, list):
                    return v
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
    """Run one web-search /responses call per item, concurrently.

    build_prompt(item) -> user prompt str
    store_fn(item, obj_or_None) -> bool   writes the cache entry; True when
                                          a real address was stored
    Returns the resolved count. Logs progress and xAI's accumulated cost."""
    found = 0
    cost = 0.0
    total = len(items)

    def _one(item):
        text, c = ai_web_search_call(client, model, system_prompt,
                                     build_prompt(item))
        return item, text, c

    with ThreadPoolExecutor(max_workers=WEB_SEARCH_WORKERS) as pool:
        futures = [pool.submit(_one, it) for it in items]
        for done, fut in enumerate(as_completed(futures), 1):
            try:
                item, text, c = fut.result()
                cost += c
                try:
                    parsed = _parse_ai_json(text)
                except json.JSONDecodeError:
                    parsed = []
                obj = next((x for x in parsed if isinstance(x, dict)), None)
            except Exception as e:
                logger.error(f"      {label} web-search error — {e}")
                continue
            if store_fn(item, obj):
                found += 1
            if done % 25 == 0:
                cache.save()
                logger.info(f"      {done}/{total} — {found} found — ~${cost:.2f}")

    cache.save()
    logger.info(f"    {label} (web search): found {found:,}/{total:,} "
                f"— cost ~${cost:.2f}")
    return found


def step_ai_lookup(df: pd.DataFrame, prev_cache, addr_cache,
                   donor_totals: pd.Series, active_tiers: list,
                   dry_run: bool = False) -> int:
    """Look up employer addresses via the AI provider (batch)."""
    try:
        client, model, provider = get_ai_client()
    except ImportError:
        logger.info("    \u26a0 openai package not installed \u2014 run: pip install openai")
        return 0
    if client is None:
        return 0

    rid = resolver_id()
    web_search = rid.endswith("+search")
    if web_search:
        logger.info("    (xAI web search enabled — Agent Tools API)")

    indiv = df[df["entity_type"] == "INDIVIDUAL"]

    tier_keys = set(donor_totals[donor_totals["tier"].isin(active_tiers)]["donor_key"])
    tier_indiv = indiv[indiv["donor_key"].isin(tier_keys)]

    def _needs_ai(key):
        cached = addr_cache.get(key)
        if cached is None:
            return True
        method = cached.get("method", "")
        if method in ("ai_error", "fec_po_box", "fec_not_found", "needs_branch_lookup"):
            return True
        # A "not found" from a different resolver earns one fresh attempt:
        # a new provider — or the same provider now armed with live web
        # search — may identify what the previous run could not. Legacy
        # not-found entries lack a "provider" tag, so they retry once.
        if method == "ai_not_found" and cached.get("provider") != rid:
            return True
        return False

    lookups = []
    seen = set()

    # Path 1 — every distinct employer from real-employer rows. The cache is
    # keyed by EMPLOYER alone (no state) — we want one corporate HQ per
    # company, not a branch per state.
    real_mask = tier_indiv["contributor_employer"].map(
        lambda v: _is_real_employer(_s(v).strip())
    )
    real_emps = (
        tier_indiv.loc[real_mask, "contributor_employer"]
        .map(lambda v: _s(v).strip())
        .replace("", pd.NA).dropna()
        .drop_duplicates()
    )
    for emp in real_emps:
        emp_norm = emp
        key = emp_norm.upper()
        if _needs_ai(key) and key not in seen:
            lookups.append((emp_norm, emp))
            seen.add(key)

    # Path 2 — RETIRED donors. Each donor has at most one previous employer
    # (via prev_cache). We feed only the employer name into the HQ lookup.
    retired_mask = tier_indiv["contributor_employer"].map(
        lambda v: _s(v).strip().upper() in RETIRED_VALUES
    )
    for _, row in tier_indiv.loc[retired_mask].drop_duplicates("donor_key").iterrows():
        state = _s(row.get("contributor_state")).strip()
        pk = _prev_key(row["contributor_name"], state)
        prev = prev_cache.get(pk)
        if prev and prev.get("employer"):
            prev_norm = _s(prev.get("employer_normalized", prev["employer"])).strip()
            if not prev_norm:
                continue
            key = prev_norm.upper()
            if _needs_ai(key) and key not in seen:
                lookups.append((prev_norm, prev["employer"]))
                seen.add(key)

    logger.info(f"    AI Lookup: {len(lookups):,} employers to resolve "
          f"({len(addr_cache):,} already cached)")

    if dry_run or not lookups:
        if dry_run and lookups:
            calls = (len(lookups) if web_search
                     else (len(lookups) + AI_BATCH_SIZE - 1) // AI_BATCH_SIZE)
            logger.info(f"    (dry run \u2014 ~{calls} API calls)")
        return 0

    lookups = sorted(lookups, key=lambda x: str(x[0]))

    # Web search path \u2014 one /responses call per employer (the model browses
    # the web), far more effective than closed-book recall on obscure firms.
    if web_search:
        def _emp_prompt(item):
            emp_norm, _ = item
            return (f'Find the US corporate headquarters address for this '
                    f'employer \u2014 the canonical HQ, not a branch:\n\n'
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
                    "confidence": obj.get("confidence", "MEDIUM"),
                })
                return True
            addr_cache.put(key, {
                "employer_address": "",
                "method": "ai_not_found",
                "provider": rid,
                "confidence": "UNKNOWN",
            })
            return False

        return run_web_search(client, model, AI_SYSTEM_PROMPT, lookups,
                              _emp_prompt, _emp_store, addr_cache, "AI Lookup")

    found = 0
    total_batches = (len(lookups) + AI_BATCH_SIZE - 1) // AI_BATCH_SIZE

    for batch_idx in range(total_batches):
        batch_start = batch_idx * AI_BATCH_SIZE
        batch = lookups[batch_start:batch_start + AI_BATCH_SIZE]

        emp_list = "\n".join(
            f'{i+1}. "{emp}"'
            for i, (emp, _) in enumerate(batch)
        )
        prompt = (
            f"Look up the US corporate headquarters address for these "
            f"{len(batch)} employers. Return the canonical HQ — NOT a regional "
            f"office or branch:\n\n{emp_list}"
        )

        try:
            # grok-4.3 measured ~112 tokens/company (visible JSON + internal
            # reasoning) on easy lookups; obscure employers reason more. The
            # budget is a ceiling — unused headroom costs nothing — so set it
            # generously to keep a batch of hard names from truncating.
            text = ai_json_call(
                client, model, provider,
                AI_SYSTEM_PROMPT, prompt,
                max_tokens=AI_BATCH_SIZE * 800,
            )
            results = _parse_ai_json(text)

            result_map = {}
            for r in results:
                if not isinstance(r, dict):
                    continue
                rname = str(r.get("name", "")).strip().upper()
                result_map[rname] = r

            batch_found = 0
            for emp_norm, emp_raw in batch:
                key = emp_norm.upper()
                r = result_map.get(emp_norm.upper()) or result_map.get(emp_raw.upper())

                if r and r.get("confidence", "UNKNOWN") != "UNKNOWN":
                    addr_cache.put(key, {
                        "employer_address": r.get("address", ""),
                        "employer_city": r.get("city", ""),
                        "employer_state": r.get("state", ""),
                        "employer_zip": r.get("zip", ""),
                        "method": ai_method(provider),
                        "confidence": r.get("confidence", "MEDIUM"),
                    })
                    batch_found += 1
                else:
                    addr_cache.put(key, {
                        "employer_address": "",
                        "method": "ai_not_found",
                        "provider": rid,
                        "confidence": "UNKNOWN",
                    })

            found += batch_found
            logger.info(f"      batch {batch_idx+1}/{total_batches}: "
                  f"{batch_found}/{len(batch)} found")

        except json.JSONDecodeError as e:
            logger.error(f" batch {batch_idx+1}: JSON parse error \u2014 {e}")
            for emp_norm, _ in batch:
                key = emp_norm.upper()
                if addr_cache.get(key) is None:
                    addr_cache.put(key, {
                        "employer_address": "",
                        "method": "ai_error",
                        "confidence": "UNKNOWN",
                    })

        except Exception as e:
            logger.error(f" batch {batch_idx+1}: API error \u2014 {e}")

        if (batch_idx + 1) % 5 == 0:
            addr_cache.save()

        time.sleep(0.5)

    addr_cache.save()
    logger.info(f"    AI Lookup: found {found:,} / {len(lookups):,} addresses")
    return found
