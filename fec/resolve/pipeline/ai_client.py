"""AI provider abstraction - xAI (Grok, OpenAI-SDK-compatible) by default; AI_PROVIDER/AI_MODEL/XAI_*/OPENAI_API_KEY env vars are read after fec.env.load_env()."""
import os

from fec.log import get_logger

logger = get_logger(__name__)

_PROVIDERS = {
    "xai":    {"model": "grok-4.3",   "base_url": "https://api.x.ai/v1", "key_env": "XAI_API_KEY"},
    "openai": {"model": "gpt-5-mini", "base_url": None,                  "key_env": "OPENAI_API_KEY"},
}

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


def get_ai_provider() -> str:
    """Active provider from AI_PROVIDER env (default xai)."""
    provider = os.environ.get("AI_PROVIDER", "xai").strip().lower()
    return provider if provider in _PROVIDERS else "xai"


def get_ai_provider_model() -> tuple:
    """Return (provider, model) without building a client - for logging."""
    provider = get_ai_provider()
    model = os.environ.get("AI_MODEL", "").strip() or _PROVIDERS[provider]["model"]
    return provider, model


def get_ai_client():
    """Build the AI client, returning (client, model, provider) - client is None when the API key is missing, which callers treat as skip-the-AI-step."""
    from openai import OpenAI

    provider, model = get_ai_provider_model()
    config = _PROVIDERS[provider]
    key = os.environ.get(config["key_env"], "").strip()
    base_url = os.environ.get("XAI_BASE_URL", "").strip() or config["base_url"]

    if not key:
        logger.info(f"    {config['key_env']} not set - AI step skipped "
                    f"(provider={provider})")
        return None, model, provider

    kwargs = {"api_key": key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs), model, provider


def ai_method(provider: str) -> str:
    """Cache method tag: every lookup is web-search-grounded, so the tag is 'ai_<provider>_search' - the trusted tier for dedup ranking and the quality-filter exemption."""
    return f"ai_{provider}_search"


def resolver_id() -> str:
    """Identity tag on not-found cache entries; always '<provider>+search', so a miss recorded by the retired closed-book path retries once under search."""
    return f"{get_ai_provider()}+search"


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
