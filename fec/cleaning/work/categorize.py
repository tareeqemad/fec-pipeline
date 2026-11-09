"""The category of an occupation text no table lists: ask the model once.

data/config/occupation_category_rules.md says, in plain words, what each
category means and which rule wins when two apply. The model reads it and
answers one category per text. Every answer is kept in
data/cache/occupation_categories.csv, so a text is sent once; a reviewed
row in data/rules/occupation_category_overrides.csv wins over the model.

Without OPENAI_API_KEY the step is skipped and the pattern fallback in
fec.config.occupation_rules.categories categorizes the new texts.
"""
from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

from fec.config.occupation_rules.categories import VALID_CATEGORIES
from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES
from fec.env import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_RULES_MD
from fec.log import get_logger

logger = get_logger(__name__)

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-5.6-sol"
BATCH = 80
WORKERS = 8
RETRIES = 4
TIMEOUT = 180


def _schema() -> dict:
    item = {
        "type": "object",
        "properties": {"text": {"type": "string"}, "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)}},
        "required": ["text", "category"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"items": {"type": "array", "items": item}},
            "required": ["items"], "additionalProperties": False}


def _ask(batch: list[str], instructions: str, model: str, key: str) -> dict[str, str]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": "Classify each text. Return every text exactly as given.\n" + "\n".join(batch)},
        ],
        "response_format": {"type": "json_schema", "json_schema": {"name": "occupations", "strict": True, "schema": _schema()}},
    }
    if model.startswith("gpt-5"):
        body["reasoning_effort"] = "low"
    for attempt in range(RETRIES):
        response = requests.post(OPENAI_URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
        if response.status_code == 200:
            items = json.loads(response.json()["choices"][0]["message"]["content"])["items"]
            return {i["text"]: i["category"] for i in items if i["text"] in batch and i["category"] in VALID_CATEGORIES}
        if response.status_code in (401, 403):
            raise RuntimeError(f"OpenAI refused the key: {response.status_code}")
        time.sleep(2 ** attempt)
    raise RuntimeError(f"OpenAI: {response.status_code} {response.text[:200]}")


def classify(texts, *, model: str | None = None) -> dict[str, str]:
    """{text: category} from the model, for texts not yet known; {} without a key."""
    key = os.environ.get("OPENAI_API_KEY", "")
    texts = sorted({t for t in texts if isinstance(t, str) and t.strip()})
    if not texts:
        return {}
    if not key:
        logger.warning("WARNING: OPENAI_API_KEY not set; %s occupation(s) categorized by patterns only", f"{len(texts):,}")
        return {}
    model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    instructions = OCCUPATION_CATEGORY_RULES_MD.read_text(encoding="utf-8")
    batches = [texts[i:i + BATCH] for i in range(0, len(texts), BATCH)]
    answers: dict[str, str] = {}
    with ThreadPoolExecutor(WORKERS) as pool:
        for got in pool.map(lambda b: _ask(b, instructions, model, key), batches):
            answers.update(got)
    return answers


def remember(answers: dict[str, str], model: str | None = None) -> None:
    """Add the answers to the cache file and to the categories in use."""
    if not answers:
        return
    model = model or os.environ.get("OPENAI_MODEL", DEFAULT_MODEL)
    new_file = not OCCUPATION_CATEGORY_CACHE_CSV.exists()
    OCCUPATION_CATEGORY_CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OCCUPATION_CATEGORY_CACHE_CSV, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if new_file:
            writer.writerow(["occupation", "category", "model"])
        for text in sorted(answers):
            writer.writerow([text, answers[text], model])
    for text, category in answers.items():
        CATEGORY_OVERRIDES.setdefault(text, category)


def learn_categories(df: pd.DataFrame, *, ask=classify) -> int:
    """Categorize the individuals' occupations no table lists; return how many texts."""
    people = df["entity_type"].eq("INDIVIDUAL") if "entity_type" in df.columns else df["is_individual"].astype(bool)
    occupation = df["contributor_occupation"].fillna("").astype(str).str.strip()
    unknown = people & occupation.ne("") & ~occupation.isin(CATEGORY_OVERRIDES)
    if not unknown.any():
        return 0
    answers = ask(occupation[unknown].unique())
    if not answers:
        return 0
    remember(answers)
    rows = unknown & occupation.isin(answers)
    df.loc[rows, "occupation_category"] = occupation[rows].map(answers)
    return len(answers)
