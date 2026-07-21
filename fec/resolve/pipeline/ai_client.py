"""AI provider abstraction for the resolve pipeline.

Default provider is xAI (Grok). xAI's API is OpenAI-SDK-compatible, so the
same `openai` client works by pointing `base_url` at the xAI endpoint.
Switch provider with the AI_PROVIDER env var.

Env vars (read AFTER helpers._load_env() has run):
    AI_PROVIDER     xai (default) | openai
    AI_MODEL        override model id (else provider default below)
    XAI_API_KEY     xAI key                 (provider=xai)
    XAI_BASE_URL    xAI endpoint            (default https://api.x.ai/v1)
    XAI_LIVE_SEARCH on -> let Grok use live web search for fresher addresses
    OPENAI_API_KEY  OpenAI key              (provider=openai)
"""
import os

from fec.log import get_logger

logger = get_logger(__name__)

_PROVIDERS = {
    "xai":    {"model": "grok-4.3",   "base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    "openai": {"model": "gpt-5-mini", "base_url": None,                  "key_env": "OPENAI_API_KEY"},
}


def get_ai_provider() -> str:
    """Active provider from AI_PROVIDER env (default xai)."""
    p = os.environ.get("AI_PROVIDER", "xai").strip().lower()
    return p if p in _PROVIDERS else "xai"


def get_ai_provider_model() -> tuple:
    """Return (provider, model) without building a client — for logging."""
    provider = get_ai_provider()
    model = os.environ.get("AI_MODEL", "").strip() or _PROVIDERS[provider]["model"]
    return provider, model


def get_ai_client():
    """Build the AI client. Returns (client, model, provider).

    client is None when the provider's API key is missing — callers
    should treat that as "skip the AI step"."""
    from openai import OpenAI

    provider, model = get_ai_provider_model()
    cfg = _PROVIDERS[provider]
    key = os.environ.get(cfg["key_env"], "").strip()
    base_url = os.environ.get("XAI_BASE_URL", "").strip() or cfg["base_url"]

    if not key:
        logger.info(f"    ⚠ {cfg['key_env']} not set — AI step skipped "
                    f"(provider={provider})")
        return None, model, provider

    kwargs = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs), model, provider


def ai_method(provider: str) -> str:
    """Cache `method` tag recording which provider resolved an address
    (e.g. 'ai_xai', 'ai_openai') — keeps provenance honest in the cache."""
    return f"ai_{provider}"


def _live_search_on() -> bool:
    """True when xAI live web search should be attached to xai calls."""
    return os.environ.get("XAI_LIVE_SEARCH", "").strip().lower() in ("1", "on", "true", "yes")


def resolver_id() -> str:
    """Identity tag stored on 'not found' cache entries. Captures the
    provider AND whether live web search was used — so a closed-book miss
    can later be retried by a search-enabled run (e.g. 'xai' vs 'xai+search')."""
    provider = get_ai_provider()
    if provider == "xai" and _live_search_on():
        return "xai+search"
    return provider


def ai_json_call(client, model: str, provider: str,
                 system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    """One JSON-mode chat completion. Returns the response text.

    Hides provider-specific params:
    - OpenAI gpt-5* models: `reasoning_effort` + `max_completion_tokens`
    - xAI grok-4: reasoning is always-on, takes plain `max_tokens`;
      optional live web search when XAI_LIVE_SEARCH is enabled.
    """
    params = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    if provider == "openai":
        params["max_completion_tokens"] = max_tokens
        params["reasoning_effort"] = "minimal"
    else:
        params["max_tokens"] = max_tokens
        if _live_search_on():
            # xAI Live Search — grounds answers in current web/X sources.
            params["extra_body"] = {"search_parameters": {"mode": "auto"}}

    resp = client.chat.completions.create(**params)
    return (resp.choices[0].message.content or "").strip()


def ai_web_search_call(client, model: str, system_prompt: str,
                       user_prompt: str) -> tuple:
    """One web-search-grounded lookup via xAI's Agent Tools API (the
    /responses endpoint). Grok may run several live web searches before
    answering — far better than closed-book recall for obscure employers.

    Returns (response_text, cost_usd). cost_usd is xAI's own figure
    (usage.cost_in_usd_ticks, 1e10 ticks = $1) and already includes the
    per-call web-search tool fees."""
    resp = client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tools=[{"type": "web_search"}],
    )
    d = resp.model_dump()
    text = ""
    for item in d.get("output", []):
        if item.get("type") == "message":
            for chunk in item.get("content", []):
                text += chunk.get("text", "") or ""
    ticks = (d.get("usage") or {}).get("cost_in_usd_ticks") or 0
    return text.strip(), ticks / 1e10
