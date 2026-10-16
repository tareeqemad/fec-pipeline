"""Run employer web searches and save progress when credits run out."""
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from functools import partial

from openai import OpenAI

from fec.log import get_logger

logger = get_logger(__name__)


PROVIDER = "openai"


DEFAULT_MODEL = "gpt-5-mini"


_CREDIT_ERROR_MARKERS = (
    'credit_balance_exhausted',
    'insufficient_quota',
    'billing_hard_limit_reached',
    'billing_not_active',
    'no credits remaining',
    'insufficient balance',
    'balance is too low',
)


class AIQuotaExhausted(RuntimeError):
    """The AI provider cannot accept more requests until credits are added."""


def is_ai_quota_error(error: Exception) -> bool:
    """True only for billing/quota errors, not a temporary 429 rate limit."""
    body = getattr(error, 'body', None)
    if isinstance(body, dict):
        details = body.get('error', body)
        if isinstance(details, dict):
            body_text = ' '.join(str(details.get(field, ''))
                                 for field in ('code', 'type', 'message'))
        else:
            body_text = str(details)
    else:
        body_text = ''

    text = f'{body_text} {error}'.lower()
    return any(marker in text for marker in _CREDIT_ERROR_MARKERS)


def get_ai_model() -> str:
    return os.environ.get("AI_MODEL", "").strip() or DEFAULT_MODEL


def get_ai_client():
    """(client, model); client is None without OPENAI_API_KEY."""
    model = get_ai_model()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        logger.info("    OPENAI_API_KEY not set - AI step skipped")
        return None, model
    return OpenAI(api_key=key), model


def ai_method() -> str:
    """Cache tag of a web-search-grounded lookup."""
    return f"ai_{PROVIDER}_search"


def resolver_id() -> str:
    """Identity tag on not-found cache entries."""
    return f"{PROVIDER}+search"


def ai_web_search_call(client, model: str, system_prompt: str,
                       user_prompt: str) -> tuple[str, float]:
    """Run one grounded Responses API lookup and return text plus reported cost."""
    response = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[{"type": "web_search"}],
    )
    data = response.model_dump()
    text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for chunk in item.get("content", []):
                text += chunk.get("text", "") or ""
    ticks = (data.get("usage") or {}).get("cost_in_usd_ticks") or 0
    return text.strip(), ticks / 1e10


WEB_SEARCH_WORKERS = 5


def _parse_ai_json(text: str) -> list | None:
    """Robustly parse JSON from AI response."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    with suppress(json.JSONDecodeError):
        result = json.loads(text)
        if isinstance(result, dict):
            for value in result.values():
                if isinstance(value, list):
                    return value
            return [result]
        return result

    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        with suppress(json.JSONDecodeError):
            return json.loads(text[start : end + 1])

    results = []
    for line in text.split("\n"):
        line = line.strip().rstrip(",")
        if line.startswith("{") and line.endswith("}"):
            with suppress(json.JSONDecodeError):
                results.append(json.loads(line))
    if results:
        return results

    raise json.JSONDecodeError("Could not parse AI response", text, 0)


def _search_item(client, model, system_prompt, build_prompt, item):
    try:
        text, cost = ai_web_search_call(
            client,
            model,
            system_prompt,
            build_prompt(item),
        )
    except Exception as error:
        if is_ai_quota_error(error):
            raise AIQuotaExhausted(
                "AI provider credits are exhausted; add credits and rerun."
            ) from error
        raise
    return item, text, cost


def _store_search_result(store_fn, item, text) -> int:
    try:
        parsed = _parse_ai_json(text)
    except json.JSONDecodeError:
        parsed = []
    result = next((entry for entry in parsed if isinstance(entry, dict)), None)
    return int(store_fn(item, result))


def _run_search_pool(search, store, items, cache, label, total):
    found = 0
    cost = 0.0
    pool = ThreadPoolExecutor(max_workers=WEB_SEARCH_WORKERS)
    futures = [pool.submit(search, item) for item in items]
    quota_error = None
    try:
        for done, future in enumerate(as_completed(futures), 2):
            try:
                item, text, call_cost = future.result()
                cost += call_cost
            except AIQuotaExhausted as error:
                quota_error = error
                break
            except Exception as error:
                logger.error(f"      {label} web-search error - {error}")
                continue

            found += store(item, text)
            if done % 25 == 0:
                cache.save()
                logger.info(f"      {done}/{total} - {found} found - ~${cost:.2f}")
    finally:
        if quota_error:
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)
        else:
            pool.shutdown(wait=True)
    return found, cost, quota_error


def run_web_search(
    client,
    model,
    system_prompt,
    items,
    build_prompt,
    store_fn,
    cache,
    label,
) -> int:
    """Resolve items concurrently, stopping when credits are exhausted."""
    if not items:
        return 0

    total = len(items)
    search = partial(
        _search_item,
        client,
        model,
        system_prompt,
        build_prompt,
    )
    store = partial(_store_search_result, store_fn)

    found = 0
    cost = 0.0
    first, remaining = items[0], items[1:]
    try:
        item, text, first_cost = search(first)
    except AIQuotaExhausted:
        raise
    except Exception as error:
        logger.error(f"      {label} web-search error - {error}")
    else:
        cost += first_cost
        found += store(item, text)

    pool_found, pool_cost, quota_error = _run_search_pool(
        search,
        store,
        remaining,
        cache,
        label,
        total,
    )
    found += pool_found
    cost += pool_cost

    if quota_error:
        cache.save()
        raise quota_error

    cache.save()
    logger.info(
        f"    {label} (web search): found {found:,}/{total:,} - cost ~${cost:.2f}"
    )
    return found
