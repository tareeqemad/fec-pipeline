"""Canonical grouping keys and cross-column re-canonicalization."""

import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _norm
from fec.cleaning.employer_synonyms.normalize import _TRAILING_PAREN_RE
from fec.cleaning.occupations.employer_groups import (
    _employer_group_key,
    employer_filers,
    employer_group_words,
    prefer_attested_spacing,
    spacing_index,
)

# COMPANY listed before CO (and INCORPORATED before INC) so iteration strips
# the longer suffix first - otherwise "MPANY" residue would be left behind.
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

# Whole-token abbreviations folded for grouping only (never display).
# ASSOC deliberately absent: ASSOCIATES vs ASSOCIATION is contextual.
_KEY_TOKEN_EXPANSIONS = {"UNIV": "UNIVERSITY", "MT": "MOUNT", "ASSOCS": "ASSOCIATES"}


# normalize an employer name into a strict grouping key
def canonical_key(name: str) -> str:
    """Strict grouping key: whitespace/punctuation/TLD/legal-suffix/THE/ampersand variants collapse to one key (KIRKLAND & ELLIS LLP -> KIRKLANDELLIS)."""
    key = name.strip().upper()
    if not key:
        return ""
    key = re.sub(r"\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b", "", key)
    # `&`/`$` -> AND, then drop the word AND entirely for grouping.
    # Word-boundary substitutions must run BEFORE collapsing whitespace,
    # otherwise the AND inside KIRKLAND would be eaten.
    key = re.sub(r"[&$]", " AND ", key)
    key = re.sub(r"^\s*THE\b", "", key)
    key = re.sub(r"\bAND\b", " ", key)
    key = " ".join(_KEY_TOKEN_EXPANSIONS.get(token, token) for token in key.split())
    key = re.sub(r"[^A-Z0-9]+", "", key)
    # iteratively strip trailing corporate suffixes until stable
    while True:
        for suffix in _CORP_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix) + 3:
                key = key[: -len(suffix)]
                break
        else:
            break
    return key


# restore each employer's most-common raw display form from raw CSV
def restore_display_suffixes(df: pd.DataFrame, raw_csv_path) -> int:
    """End-of-cleaning pass: restore each employer's most-common raw suffix-bearing form (per canonical_key) so the saved CSV reads naturally; idempotent."""
    if not Path(raw_csv_path).exists():
        return 0

    raw = pd.read_csv(
        raw_csv_path,
        usecols=lambda column: column in ("contributor_employer", "contributor_name"),
        dtype=str,
        keep_default_na=False,
        low_memory=False,
    )
    # uppercase, collapse whitespace and `+`, drop ticker tails "(ITCI)" and stray
    # trailing dots so the restored display form carries no FEC keying junk
    raw["emp"] = (
        raw["contributor_employer"]
        .str.strip()
        .str.upper()
        .str.replace(r"[\s+]+", " ", regex=True)
        .str.replace(_TRAILING_PAREN_RE, "", regex=True)
        .str.replace(r"(?:\s*\.)+\s*$", "", regex=True)
        .str.strip()
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

    indiv_idx = _indiv_idx(df)
    current = _norm(df.loc[indiv_idx, "contributor_employer"])
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


# prefer the spacing variant filers actually wrote over raw mode
def _prefer_attested_spacing_forms(raw: pd.DataFrame, most_common_form: dict) -> None:
    """Keep the display form's word breaks consistent with occ_canonicalize_employers: a raw mode 'TWINCITY FAN' yields to 'TWIN CITY FAN' when the same filers write 'TWIN CITY FAN COMPANIES LTD'."""
    if "contributor_name" not in raw.columns:
        return
    # imported here: the occupations package imports this one at load time

    candidates = {}
    for key, forms in raw.groupby("key")["emp"].unique().items():
        if len(forms) < 2:
            continue
        # only the mode's strict spacing variants compete, never a different
        # name that merely shares the looser canonical_key
        strict = _employer_group_key(most_common_form[key])
        siblings = [form for form in forms if _employer_group_key(form) == strict]
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


# tally employer name occurrences across given columns for individuals
def _employer_counts(df: pd.DataFrame, indiv_idx, columns: list[str]):
    series = []
    current_counts = pd.Series(dtype="int64")
    for column in columns:
        values = df.loc[indiv_idx, column]
        values = values[_norm(values) != ""]
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


# apply the variant-to-canonical mapping to each employer column
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


# merge employer variants that later cleaning steps introduced
def _recanonicalize_employers(df: pd.DataFrame) -> int:
    """Unify employer variants created by later cleaning steps."""
    columns = [
        column
        for column in ("contributor_employer", "previous_employer")
        if column in df.columns
    ]
    counts, current_counts = _employer_counts(df, _indiv_idx(df), columns)
    if counts.empty:
        return 0

    mapping = _canonical_employer_mapping(counts, current_counts)
    return _apply_employer_mapping(df, columns, mapping) if mapping else 0
