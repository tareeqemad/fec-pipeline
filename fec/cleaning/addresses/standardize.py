"""One standard form for a filed US address: the model reads it once.

data/config/address_standard_rules.md says, in plain words, what the
standard form is (USPS Publication 28 abbreviations, the unit in street_2,
the city in full, a five-digit ZIP). The model reads every distinct filed
address once; its answer is kept in data/cache/addresses.csv under the
address as filed, so a filing is never sent twice. Reviewed corrections in
data/rules/address_rules.csv are applied afterwards and win.

Foreign addresses are left as filed. Without OPENAI_API_KEY an address not
in the cache stays as filed, uppercased.
"""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd

from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.cleaning.model import ask_lines, model_name
from fec.env import ADDRESS_CACHE_CSV, ADDRESS_STANDARD_RULES_MD
from fec.log import get_logger

logger = get_logger(__name__)

FIELDS = ("street_1", "street_2", "city", "state", "zip")
COLUMNS = tuple(f"contributor_{field}" for field in FIELDS)
_ITEM = {
    "type": "object",
    "properties": {"filed": {"type": "string"}, **{field: {"type": "string"} for field in FIELDS}},
    "required": ["filed", *FIELDS],
    "additionalProperties": False,
}


def filed_keys(df: pd.DataFrame) -> pd.Series:
    """The address as filed, one line: street_1 | street_2 | city | state | zip."""
    parts = [df[column].fillna("").astype(str).str.upper().str.strip() for column in COLUMNS]
    return parts[0].str.cat(parts[1:], sep=" | ")


def _read_cache() -> dict[str, dict]:
    if not ADDRESS_CACHE_CSV.exists():
        return {}
    with open(ADDRESS_CACHE_CSV, encoding="utf-8", newline="") as handle:
        return {row["filed"]: row for row in csv.DictReader(handle)}


def _append_cache(answers: dict[str, dict], model: str) -> None:
    new_file = not ADDRESS_CACHE_CSV.exists()
    ADDRESS_CACHE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(ADDRESS_CACHE_CSV, "a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        if new_file:
            writer.writerow(["filed", *FIELDS, "model"])
        for filed in sorted(answers):
            item = answers[filed]
            writer.writerow([filed, *(str(item[field]).strip().upper() for field in FIELDS), model])


def _with_standard_forms(answers: dict[str, dict]) -> dict[str, dict]:
    """The answers, plus each standard form mapped to itself, so a cleaned row is never re-asked."""
    full = {k: {f: str(v[f]).strip().upper() for f in FIELDS} for k, v in answers.items()}
    for fields in list(full.values()):
        standard = " | ".join(fields[f] for f in FIELDS)
        full.setdefault(standard, dict(fields))
    return full


def standardize(filed, *, model: str | None = None) -> dict[str, dict]:
    """{filed line: standard fields} from the model; {} without a key."""
    return ask_lines(filed, ADDRESS_STANDARD_RULES_MD.read_text(encoding="utf-8"), _ITEM, key_field="filed",
                     batch=40, model=model)


def standardize_addresses(df: pd.DataFrame, *, ask=standardize) -> int:
    """Rewrite every US address in its standard form; return how many filings changed."""
    us = ~foreign_address_mask(df)
    keys = filed_keys(df)
    cache = _read_cache()
    unknown = sorted(set(keys[us]) - set(cache))
    if unknown:
        answers = ask(unknown)
        if answers:
            learnt = {k: v for k, v in _with_standard_forms(answers).items() if k not in cache}
            _append_cache(learnt, model_name())
            cache.update(learnt)
            logger.info("Addresses read by the model: %s", f"{len(answers):,}")
    known = us & keys.isin(cache)
    if not known.any():
        return 0
    before = df.loc[known, list(COLUMNS)].fillna("").astype(str)
    for field, column in zip(FIELDS, COLUMNS):
        values = keys[known].map(lambda k, field=field: cache[k][field])
        df.loc[known, column] = values.replace("", np.nan).astype(object)
    after = df.loc[known, list(COLUMNS)].fillna("").astype(str)
    return int((before != after).any(axis=1).sum())


# street_1 that only repeats the donor's own name is no street
def clear_own_name_street(df: pd.DataFrame) -> int:
    street = df["contributor_street_1"].fillna("").astype(str).str.upper()
    first = df["contributor_first_name"].fillna("").astype(str).str.strip().str.upper()
    last = df["contributor_last_name"].fillna("").astype(str).str.strip().str.upper()
    is_name = (
        ((street == first) & (first.str.len() >= 3))
        | ((street == last) & (last.str.len() >= 3))
        | (street == first + " " + last)
        | (street == last + " " + first)
    )
    if is_name.any():
        df.loc[is_name, "contributor_street_1"] = np.nan
    return int(is_name.sum())
