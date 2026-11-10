"""The category of an occupation: the reviewed table first, else ask the model once."""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable

import pandas as pd

from fec.cleaning.work.model_client import ask_lines, model_name
from fec.common.log import get_logger
from fec.common.settings import OCCUPATION_CATEGORY_CACHE_CSV, OCCUPATION_CATEGORY_RULES_MD
from fec.vocabulary.occupations import CATEGORY_OVERRIDES, VALID_CATEGORIES

logger = get_logger(__name__)

_ITEM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "category": {"type": "string", "enum": sorted(VALID_CATEGORIES)}},
    "required": ["text", "category"],
    "additionalProperties": False,
}


def ask_model_for_categories(texts: Iterable[object], *, model: str | None = None) -> dict[str, str]:
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


def save_model_categories(answers: dict[str, str], model: str | None = None) -> None:
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


def learn_categories(df: pd.DataFrame, *, ask: Callable[..., dict[str, str]] = ask_model_for_categories) -> int:
    """Categorize the individuals' occupations no table lists; return how many texts."""
    people = df["entity_type"].eq("INDIVIDUAL") if "entity_type" in df.columns else df["is_individual"].astype(bool)
    occupation = df["contributor_occupation"].fillna("").astype(str).str.strip()
    unknown = people & occupation.ne("") & ~occupation.isin(CATEGORY_OVERRIDES)
    if not unknown.any():
        return 0
    answers = ask(occupation[unknown].unique())
    if not answers:
        return 0
    save_model_categories(answers)
    rows = unknown & occupation.isin(answers)
    df.loc[rows, "occupation_category"] = occupation[rows].map(answers)
    return len(answers)


def lookup_categories(occupation_series: pd.Series) -> pd.Series:
    """Occupation category: the listed one, else OTHER until the model reads the text."""
    result = occupation_series.map(CATEGORY_OVERRIDES).astype(object)
    return result.where(result.notna(), "OTHER").where(occupation_series.notna(), pd.NA)


def categorize_occupations(occupation_series: pd.Series) -> pd.Series:
    """Return the category of final cleaned occupations."""
    return lookup_categories(occupation_series.fillna("").astype(str).str.strip().str.upper())
