"""One call to the model: an instruction file, lines of text, structured answers.

The key comes from OPENAI_API_KEY; without it ask_lines returns {} and the
caller falls back to whatever it did before the model.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import requests

from fec.common.log import get_logger

logger = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_MODEL = "gpt-5.6-sol"

WORKERS = 8

RETRIES = 4

TIMEOUT = 300

BATCH_SIZE = 80


def model_name(model: str | None = None) -> str:
    """Return the model to call: the one given, OPENAI_MODEL, or the default."""
    return model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)


def have_key() -> bool:
    """Return whether an OpenAI API key is set."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def _ask(
    lines: list[str], instructions: str, user_prefix: str, schema: dict, key_field: str, model: str
) -> dict[str, dict]:
    """Send one batch of lines to the model and return the answers keyed by line."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_prefix + "\n".join(lines)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answers", "strict": True, "schema": schema},
        },
    }
    if model.startswith("gpt-5"):
        body["reasoning_effort"] = "low"
    headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    wanted = set(lines)
    for attempt in range(RETRIES):
        response = requests.post(OPENAI_URL, json=body, headers=headers, timeout=TIMEOUT)
        if response.status_code == 200:
            items = json.loads(response.json()["choices"][0]["message"]["content"])["items"]
            return {item[key_field]: item for item in items if item.get(key_field) in wanted}
        if response.status_code in (401, 403):
            raise RuntimeError(f"OpenAI refused the key: {response.status_code}")
        if response.status_code == 429 and "insufficient_quota" in response.text:
            raise OutOfCredit("the OpenAI account has no credits left")
        time.sleep(2**attempt)
    raise RuntimeError(f"OpenAI: {response.status_code} {response.text[:200]}")


class OutOfCredit(RuntimeError):
    """The account cannot pay for the call; the caller falls back and says so."""


def _build_answers_schema(item_schema: dict) -> dict:
    """Return the schema of one answer: an object holding a list of answer items."""
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": item_schema}},
        "required": ["items"],
        "additionalProperties": False,
    }


def ask_lines(
    lines: Iterable[object],
    instructions: str,
    item_schema: dict,
    *,
    key_field: str,
    user_prefix: str = "",
    model: str | None = None,
) -> dict[str, dict]:
    """{line: answer item} for every line the model answered; {} without a key.

    item_schema describes one answer; key_field is the property that echoes the line.
    The lines are sent BATCH_SIZE at a time.
    """
    lines = sorted({line for line in lines if isinstance(line, str) and line.strip()})
    if not lines:
        return {}
    if not have_key():
        logger.warning("WARNING: OPENAI_API_KEY not set; %s line(s) left as they are", f"{len(lines):,}")
        return {}
    schema = _build_answers_schema(item_schema)
    batches = [lines[i : i + BATCH_SIZE] for i in range(0, len(lines), BATCH_SIZE)]
    answers: dict[str, dict] = {}
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            # pool.map passes one batch; the lambda adds the arguments every call shares.
            for batch_answers in pool.map(
                lambda batch: _ask(batch, instructions, user_prefix, schema, key_field, model_name(model)),
                batches,
            ):
                answers.update(batch_answers)
        missing = [line for line in lines if line not in answers]
        if missing:
            answers.update(_ask(missing, instructions, user_prefix, schema, key_field, model_name(model)))
    except OutOfCredit as problem:
        logger.warning(
            "WARNING: %s; %s of %s line(s) read, the rest left as they are",
            problem,
            f"{len(answers):,}",
            f"{len(lines):,}",
        )
    return answers
