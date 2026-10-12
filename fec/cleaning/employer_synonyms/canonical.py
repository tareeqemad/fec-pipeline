"""Canonical grouping keys and cross-column re-canonicalization."""

import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from fec.cleaning._helpers import individual_index, upper_text
from fec.cleaning.employer_synonyms.normalize import TRAILING_PAREN_RE
from fec.cleaning.occupations.employer_groups import (
    employer_group_key,
    employer_filers,
    employer_group_words,
    prefer_attested_spacing,
    spacing_index,
)

# longer suffixes first, else "MPANY" residue stays
_CORP_SUFFIXES = (
    "INCORPORATED",
    "CORPORATION",
    "COMPANY",
    "ENTERPRISES",
    "PLLC",
    "CORP",
    "GROUP",
    "LLC",
    "LLP",
    "INC",
    "LTD",
    "CO",
    "PC",
    "PA",
    "LP",
)
# short suffixes usually brand when glued on
_GLUED_BRAND_SUFFIXES = frozenset({"CO", "PC", "PA", "LP"})

# grouping-only abbreviations; ASSOC is contextual, so absent
_KEY_TOKEN_EXPANSIONS = {"UNIV": "UNIVERSITY", "MT": "MOUNT", "ASSOCS": "ASSOCIATES"}


# strict grouping key for an employer name
def canonical_key(name: str) -> str:
    key = name.strip().upper()
    if not key:
        return ""
    key = re.sub(r"\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b", "", key)
    # word subs must precede collapsing whitespace
    key = re.sub(r"[&$]", " AND ", key)
    key = re.sub(r"^\s*THE\b", "", key)
    key = re.sub(r"\bAND\b", " ", key)
    key = " ".join(_KEY_TOKEN_EXPANSIONS.get(token, token) for token in key.split())
    # a lone word keeps glued CO/PC/PA/LP
    words = re.findall(r"[A-Z0-9]+", key)
    while len(words) > 1 and words[-1] in _CORP_SUFFIXES:
        words.pop()
    brand_length = len(words[0]) if len(words) == 1 else 0
    key = re.sub(r"[^A-Z0-9]+", "", key)
    # iteratively strip trailing corporate suffixes until stable
    while True:
        for suffix in _CORP_SUFFIXES:
            cuts_brand = len(key) - len(suffix) < brand_length
            if cuts_brand and suffix in _GLUED_BRAND_SUFFIXES:
                continue
            if key.endswith(suffix) and len(key) > len(suffix) + 3:
                key = key[: -len(suffix)]
                break
        else:
            break
    return key


# tidy comma spacing; numbers like 1,000 kept
def tidy_employer_commas(name):
    if not isinstance(name, str) or "," not in name:
        return name
    return re.sub(r"\s*,(?=[A-Z])", ", ", re.sub(r"\s+,", ",", name))


# restore most common raw display form; idempotent
def restore_display_suffixes(df: pd.DataFrame, raw_csv_path) -> int:
    if not Path(raw_csv_path).exists():
        return 0

    raw = pd.read_csv(
        raw_csv_path,
        usecols=lambda column: column in ("contributor_employer", "contributor_name"),
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )
    # strip FEC keying junk from display forms
    raw["emp"] = (
        raw["contributor_employer"]
        .str.strip()
        .str.upper()
        .str.replace(r"[\s+]+", " ", regex=True)
        .str.replace(TRAILING_PAREN_RE, "", regex=True)
        .str.replace(r"(?:\s*\.)+\s*$", "", regex=True)
        .str.strip()
        .map(tidy_employer_commas)
    )
    raw = raw[raw["emp"] != ""]
    raw["key"] = raw["emp"].map(canonical_key)
    raw = raw[raw["key"] != ""]

    if raw.empty:
        return 0

    most_common_form = (
        raw.groupby("key")["emp"].agg(lambda values: values.mode().iloc[0]).to_dict()
    )
    _prefer_attested_spacing_forms(raw, most_common_form)

    indiv_idx = individual_index(df)
    current = upper_text(df.loc[indiv_idx, "contributor_employer"])
    has_emp = current != ""
    target = indiv_idx[has_emp]
    if len(target) == 0:
        return 0

    current_clean = current[has_emp]
    keys = current_clean.map(canonical_key)
    new_values = keys.map(most_common_form).fillna(current_clean)

    changed_mask = new_values != current_clean
    n_changed = int(changed_mask.sum())
    if n_changed:
        df.loc[target[changed_mask], "contributor_employer"] = new_values[changed_mask]
    return n_changed


# prefer spacing the same filers actually wrote
def _prefer_attested_spacing_forms(raw: pd.DataFrame, most_common_form: dict) -> None:
    if "contributor_name" not in raw.columns:
        return

    candidates = {}
    for key, forms in raw.groupby("key")["emp"].unique().items():
        if len(forms) < 2:
            continue
        # only the mode's strict spacing variants compete
        strict = employer_group_key(most_common_form[key])
        siblings = [form for form in forms if employer_group_key(form) == strict]
        if len({employer_group_words(form) for form in siblings}) > 1:
            candidates[key] = siblings
    if not candidates:
        return

    counts = raw["emp"].value_counts()
    filers = employer_filers(raw["emp"], raw["contributor_name"])
    index = spacing_index(counts.index)
    for key, siblings in candidates.items():
        most_common_form[key] = prefer_attested_spacing(
            most_common_form[key], siblings, counts, filers, index,
        )


# count individuals' employer names across columns
def _employer_counts(df: pd.DataFrame, indiv_idx, columns: list[str]):
    series = []
    current_counts = pd.Series(dtype="int64")
    for column in columns:
        values = df.loc[indiv_idx, column]
        values = values[upper_text(values) != ""]
        series.append(values)
        if column == "contributor_employer":
            current_counts = values.value_counts()
    if not series:
        return pd.Series(dtype="int64"), current_counts
    all_names = pd.concat(series, ignore_index=True)
    return all_names.value_counts(), current_counts


# build variant-to-canonical-name mapping grouped by canonical key
def _canonical_employer_mapping(counts, current_counts) -> dict:
    groups = defaultdict(list)
    for name in counts.index:
        key = canonical_key(name)
        if key:
            groups[key].append(name)

    mapping = {}
    for variants in groups.values():
        if len(variants) < 2:
            continue
        canonical = sorted(
            variants,
            key=lambda variant: (
                -int(current_counts.get(variant, 0) > 0),
                -current_counts.get(variant, 0),
                -counts.get(variant, 0),
                -len(variant),
                variant,
            ),
        )[0]
        for variant in variants:
            if variant != canonical:
                mapping[variant] = canonical
    return mapping


# apply the mapping to each employer column
def _apply_employer_mapping(
    df: pd.DataFrame,
    columns: list[str],
    mapping: dict,
) -> int:
    changed = 0
    for column in columns:
        to_fix = df[column].isin(mapping)
        n_col = int(to_fix.sum())
        if n_col:
            df.loc[to_fix, column] = df.loc[to_fix, column].map(mapping)
            changed += n_col
    return changed


# merge employer variants later steps introduced
def recanonicalize_employers(df: pd.DataFrame) -> int:
    columns = [
        column
        for column in ("contributor_employer", "previous_employer")
        if column in df.columns
    ]
    counts, current_counts = _employer_counts(df, individual_index(df), columns)
    if counts.empty:
        return 0

    mapping = _canonical_employer_mapping(counts, current_counts)
    return _apply_employer_mapping(df, columns, mapping) if mapping else 0
