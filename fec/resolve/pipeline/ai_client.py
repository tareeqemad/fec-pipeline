"""OpenAI client for employer lookups; reads OPENAI_API_KEY and AI_MODEL."""
import os

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
    from openai import OpenAI

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
