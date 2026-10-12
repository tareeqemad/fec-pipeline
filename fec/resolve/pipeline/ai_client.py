"""OpenAI client for employer lookups."""
import os

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
    """Raised when AI credits run out."""


# true for billing errors, not rate limits
def is_ai_quota_error(error: Exception) -> bool:
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


# configured AI model name, or the default
def get_ai_model() -> str:
    return os.environ.get("AI_MODEL", "").strip() or DEFAULT_MODEL


# OpenAI client and model; None without key
def get_ai_client():
    model = get_ai_model()
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        logger.info("    OPENAI_API_KEY not set - AI step skipped")
        return None, model
    return OpenAI(api_key=key), model


# cache tag identifying a web-search-grounded AI lookup
def ai_method() -> str:
    return f"ai_{PROVIDER}_search"


# identity tag written on not-found cache entries
def resolver_id() -> str:
    return f"{PROVIDER}+search"


# one web-search AI call; text and cost
def ai_web_search_call(client, model: str, system_prompt: str,
                       user_prompt: str) -> tuple[str, float]:
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
