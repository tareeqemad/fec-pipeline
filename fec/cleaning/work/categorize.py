"""The category of an occupation text no table lists: ask the model once.

data/config/occupation_category_rules.md says, in plain words, what each
category means and which rule wins when two apply. The model reads it and
answers one category per text. Every answer is kept in
data/cache/occupation_categories.csv, so a text is sent once; a reviewed
row in data/rules/occupation_category_overrides.csv wins over the model.

Without OPENAI_API_KEY the step is skipped and a new text stays OTHER.
"""

from __future__ import annotations

import csv

import pandas as pd

from fec.cleaning.model import (  # noqa: F401  (DEFAULT_MODEL: the name tests and callers know)
    DEFAULT_MODEL,
    ask_lines,
    model_name,
)
from fec.config.occupation_rules.categories import VALID_CATEGORIES
from fec.config.occupation_rules.rules import CATEGORY_OVERRIDES
from fec.env import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_RULES_MD
from fec.log import get_logger

logger = get_logger(__name__)

_ITEM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)}},
    "required": ["text", "category"],
    "additionalProperties": False,
}


def classify(texts, *, model: str | None = None) -> dict[str, str]:
    """{text: category} from the model, for texts not yet known; {} without a key."""
    answers = ask_lines(
        texts,
        OCCUPATION_CATEGORY_RULES_MD.read_text(encoding="utf-8"),
        _ITEM,
        key_field="text",
        user_prefix="Classify each text. Return every text exactly as given.\n",
        model=model,
    )
    return {text: item["category"] for text, item in answers.items() if item["category"] in VALID_CATEGORIES}


def remember(answers: dict[str, str], model: str | None = None) -> None:
    """Add the answers to the cache file and to the categories in use."""
    if not answers:
        return
    model = model_name(model)
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
