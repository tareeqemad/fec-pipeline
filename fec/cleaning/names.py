"""Read name rules and clean contributor names."""
from __future__ import annotations

import csv
import re
from fec.env import CONTRIBUTOR_NAME_RULES_CSV
import numpy as np
import pandas as pd
from fec.config.data import (
    COMM_TAIL_RE,
    PRO_SUFFIX_RE,
    SUFFIX_RE,
    TITLE_RE,
)
from fec.cleaning.reports.trail import NAME_FIELDS, AuditTrail


RULE_TYPES = {
    "exact_name",
    "sub_id",
    "first_name",
    "committee_name",
}

_ZIP5_RE = re.compile(r"\d{5}")


def _first_name_rule(
    number: int, raw: str, corrected: str, zip_field: str
) -> list[tuple[str, str, str]]:
    """Parse one first_name rule into (surname, garbled word, zip5) keys."""
    last, comma, first = raw.partition(",")
    last, first = last.strip(), first.strip()
    if not comma or "," in first or not last or not first or len(first.split()) != 1:
        raise ValueError(
            f"first_name rule on row {number} must read 'SURNAME, GARBLED': {raw}"
        )
    if "," in corrected or len(corrected.split()) != 1:
        raise ValueError(
            f"first_name rule on row {number} must correct one word: {corrected}"
        )
    zips = [part.strip() for part in zip_field.split("|")] if zip_field else []
    if not zips or not all(_ZIP5_RE.fullmatch(z) for z in zips):
        raise ValueError(
            f"first_name rule on row {number} needs the filer's ZIP5 "
            f"('|'-separated): {zip_field!r}"
        )
    return [(last, first, z) for z in zips]


def _load_rules(path=CONTRIBUTOR_NAME_RULES_CSV) -> dict[str, dict]:
    if not path.exists():
        raise FileNotFoundError(f"Missing contributor name rules: {path}")

    rules = {rule_type: {} for rule_type in RULE_TYPES}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            rule_type = (row.get("rule_type") or "").strip()
            raw = (row.get("raw_value") or "").strip()
            corrected = (row.get("corrected_value") or "").strip()
            source = (row.get("source") or "").strip()
            zip_field = (row.get("zip5") or "").strip()
            if rule_type not in RULE_TYPES:
                raise ValueError(
                    f"Invalid name rule type on row {number}: {rule_type}"
                )
            if not raw or not corrected or not source:
                raise ValueError(f"Invalid name rule on row {number}")
            if rule_type == "first_name":
                keys = _first_name_rule(number, raw, corrected, zip_field)
            elif zip_field:
                raise ValueError(
                    f"zip5 only scopes first_name rules (row {number}, {rule_type})"
                )
            else:
                keys = [raw]
            for key in keys:
                if key in rules[rule_type]:
                    raise ValueError(
                        f"Duplicate {rule_type} rule on row {number}: {raw}"
                    )
                rules[rule_type][key] = corrected
    return rules


_RULES = _load_rules()
EXACT_NAME_CORRECTIONS = _RULES["exact_name"]
ROW_NAME_CORRECTIONS = _RULES["sub_id"]
# (surname, garbled first word, zip5) -> corrected first word
FIRST_NAME_FIXES = _RULES["first_name"]
COMMITTEE_NAME_FIXES = _RULES["committee_name"]


# professional/religious titles appearing after the comma in a name
_NAME_TITLE_RE = re.compile(
    r',\s*(DR\.?|RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|'
    r'IMAM|REV\.?|REVEREND|JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|AMB\.?|AMBASSADOR)\s',
    re.IGNORECASE,
)


# LAST, <titles/suffixes>, FIRST: WIENIR, MD, MICHAEL
_EMBEDDED_TITLE_RE = re.compile(
    r'^([A-Z][A-Z\'\-]+)\s*,?\s*'
    r'(?:,?\s*(?:MD|M\.?D\.?|PHD|PH\.?D\.?|DDS|DPM|JR\.?|SR\.?|ESQ\.?|II|III|IV|FAAOS|FACS|DO|DVM)\s*)*'
    r',\s*(.+)$',
    re.IGNORECASE
)


_NAN_REPLACE = {'nan': np.nan, 'None': np.nan, '': np.nan}


def _preclean_name_punctuation(df: pd.DataFrame) -> None:
    """Remove backticks, semicolons, stray dots, collapse double commas."""
    df['contributor_name'] = (
        df['contributor_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(r'^\.+', '', regex=True)
        # Remove dots after the comma, keeping dotted tails like "N.A.".
        .str.replace(r',\s*\.+\s*', ', ', regex=True)
        .str.replace(r',{2,}', ',', regex=True)
        # trailing dash ("LEVY, ALLAN -") would leak into first_name
        .str.replace(r'\s*-\s*$', '', regex=True)
        # Separate "W.DAVID" while keeping abbreviations like "N.A.".
        .str.replace(r'\b([A-Z])\.([A-Z]{2,})', r'\1 \2', regex=True)
        .str.strip()
        .str.replace(r'\s+', ' ', regex=True)
    )
    df['contributor_first_name'] = (
        df['contributor_first_name'].astype('string')
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(r'^\.+\s*', '', regex=True)
        .str.strip()
        .replace('', pd.NA)
    )


def _remove_name_titles(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Remove titles from names without changing work fields."""
    for idx in df.loc[is_individual].index:
        name = str(df.at[idx, 'contributor_name'])
        match = _NAME_TITLE_RE.search(name)
        if not match:
            continue

        clean_name = name[:match.start(1)] + name[match.end(1):]
        clean_name = re.sub(r',\s*,', ',', clean_name)
        clean_name = re.sub(r'\s{2,}', ' ', clean_name).strip()
        df.at[idx, 'contributor_name'] = clean_name


def _handle_multi_comma_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Multi-comma names with titles: "WIENIR, MD, MICHAEL"."""
    multi_comma_mask = is_individual & (df['contributor_name'].str.count(',') > 1)
    for idx in df.loc[multi_comma_mask].index:
        match = _EMBEDDED_TITLE_RE.match(df.at[idx, 'contributor_name'])
        if match:
            clean_last = match.group(1).strip()
            clean_first = match.group(2).strip().rstrip('.')
            df.at[idx, 'contributor_last_name'] = clean_last
            df.at[idx, 'contributor_first_name'] = clean_first
            df.at[idx, 'contributor_name'] = f"{clean_last}, {clean_first}"


def _split_missing_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Split LAST, FIRST for reclassified records missing name parts."""
    needs_split = is_individual & df['contributor_first_name'].isna()
    if needs_split.any():
        names = df.loc[needs_split, 'contributor_name'].astype(str)
        has_comma = names.str.contains(',', na=False, regex=False)
        comma_rows = needs_split & has_comma.reindex(needs_split.index, fill_value=False)

        if comma_rows.any():
            split = df.loc[comma_rows, 'contributor_name'].str.split(',', n=1, expand=True)
            df.loc[comma_rows, 'contributor_last_name'] = split[0].str.strip()
            df.loc[comma_rows, 'contributor_first_name'] = split[1].str.strip()

    missing_last = (
        is_individual
        & df['contributor_last_name'].isna()
        & df['contributor_name'].str.contains(',', na=False, regex=False)
    )
    if missing_last.any():
        split2 = df.loc[missing_last, 'contributor_name'].str.split(',', n=1, expand=True)
        df.loc[missing_last, 'contributor_last_name'] = split2[0].str.strip()


def _clean_committee_names(df: pd.DataFrame, is_committee: pd.Series) -> None:
    """Clear first/last for committees, strip trailing junk from committee names."""
    df.loc[is_committee, 'contributor_first_name'] = np.nan
    df.loc[is_committee, 'contributor_last_name'] = np.nan
    df.loc[is_committee, 'contributor_name'] = (
        df.loc[is_committee, 'contributor_name'].astype(str)
        .str.replace(COMM_TAIL_RE, '', regex=True)
        .str.strip()
        .str.rstrip(',')
        .str.strip()
        .replace(COMMITTEE_NAME_FIXES)
    )


def _strip_individual_titles_suffixes(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Strip first-name titles, last-name suffixes and emails."""
    df.loc[is_individual, 'contributor_first_name'] = (
        df.loc[is_individual, 'contributor_first_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(TITLE_RE, '', regex=True)
        # Separate the initial: "W.DAVID" -> "W. DAVID".
        .str.replace(r'\b([A-Z])\.(?=[A-Z]{2})', r'\1. ', regex=True)
        .str.strip()
        .replace(_NAN_REPLACE)
    )

    email_in_last = (
        is_individual &
        df['contributor_last_name'].fillna('').str.contains('@', regex=False)
    )
    for idx in df.loc[email_in_last].index:
        full_name = str(df.at[idx, 'contributor_name'])
        if ',' in full_name:
            last_part = full_name.split(',', 1)[0].strip()
            if '@' not in last_part:
                df.at[idx, 'contributor_last_name'] = last_part
            else:
                prefix = str(df.at[idx, 'contributor_last_name']).split('@')[0].upper()
                prefix = re.sub(r'[.\d]', '', prefix)
                if len(prefix) >= 3:
                    df.at[idx, 'contributor_last_name'] = prefix

    df.loc[is_individual, 'contributor_last_name'] = (
        df.loc[is_individual, 'contributor_last_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(SUFFIX_RE, '', regex=True)
        .str.replace(PRO_SUFFIX_RE, '', regex=True)
        .str.replace(PRO_SUFFIX_RE, '', regex=True)  # second pass for stacked (MD FACS)
        .str.strip()
        .str.rstrip(',')
        .str.strip()
        .replace(_NAN_REPLACE)
    )


_PURE_TITLE = {'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.', 'DR', 'DR.',
               'MD', 'M.D.', 'PHD', 'PH.D.', 'ESQ', 'ESQ.',
               'DDS', 'DVM', 'JR', 'JR.', 'SR', 'SR.'}


def _text(value) -> str:
    """Return stripped text, or an empty string for missing values."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ''
    return str(value).strip()


def _fix_garbled_first_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Verified first-name fixes for the matching surname and ZIP5."""
    if not FIRST_NAME_FIXES:
        return
    garbled_words = {word for _, word, _ in FIRST_NAME_FIXES}
    first_word = df['contributor_first_name'].fillna('').astype(str).str.split().str[0]
    candidates = is_individual & first_word.isin(garbled_words)
    if not candidates.any():
        return
    zip5 = (
        df.loc[candidates, 'contributor_zip'].astype('string').fillna('')
        .str.replace(r'\D', '', regex=True).str[:5]
    )
    for idx in df.index[candidates]:
        old_first = _text(df.at[idx, 'contributor_first_name'])
        last = _text(df.at[idx, 'contributor_last_name'])
        old_first_word = old_first.split()[0]
        new_first_word = FIRST_NAME_FIXES.get((last, old_first_word, zip5.at[idx]))
        if not new_first_word or not last:
            continue
        rest = old_first[len(old_first_word):].strip()
        new_first = f"{new_first_word} {rest}" if rest else new_first_word
        df.at[idx, 'contributor_first_name'] = new_first
        df.at[idx, 'contributor_name'] = f"{last}, {new_first}"


# a plain given-name word: letters, apostrophes, hyphens, e.g. "O'BRIEN"
_GIVEN_WORD_RE = re.compile(r"[A-Z][A-Z'-]+")


def _keep_filed_given_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Keep extra filed given names: WOLF in 'HARRIS, S. WOLF'."""
    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    last = df['contributor_last_name'].fillna('').astype(str).str.strip()
    filed = df['contributor_name'].fillna('').astype(str).str.partition(',')
    filed_last, filed_first = filed[0].str.strip(), filed[2].str.strip()
    candidates = is_individual & (first != '') & (filed_last == last) & (filed_first != first)
    for index in df.index[candidates]:
        if _adds_given_names(first.at[index], filed_first.at[index]):
            df.at[index, 'contributor_first_name'] = filed_first.at[index]


def _adds_given_names(first: str, filed_first: str) -> bool:
    """The filed first part: the field plus whole name words."""
    field_words, filed_words = first.split(), filed_first.split()
    added = filed_words[len(field_words):]
    return (
        bool(added)
        and filed_words[:len(field_words)] == field_words
        and all(
            _GIVEN_WORD_RE.fullmatch(word)
            and word not in _PURE_TITLE
            and word not in field_words  # "RANDALL RANDALL" repeats, adds nothing
            for word in added
        )
    )


def _fix_title_as_first_name(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Clear first names that are titles (MRS, DR, MD)."""
    title_as_first = (
        is_individual &
        df['contributor_first_name'].fillna('').str.upper().isin(_PURE_TITLE)
    )
    if title_as_first.any():
        df.loc[title_as_first, 'contributor_first_name'] = np.nan


def _fix_compound_last_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Split compound last names: BRIAN KROST becomes first=BRIAN, last=KROST."""
    compound_last = (
        is_individual &
        df['contributor_first_name'].isna() &
        df['contributor_last_name'].fillna('').str.contains(' ', regex=False)
    )
    for idx in df.loc[compound_last].index:
        parts = str(df.at[idx, 'contributor_last_name']).strip().split()
        if len(parts) == 2:
            df.at[idx, 'contributor_first_name'] = parts[0]
            df.at[idx, 'contributor_last_name'] = parts[1]
            df.at[idx, 'contributor_name'] = f"{parts[1]}, {parts[0]}"


def _rebuild_contributor_name(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Rebuild contributor_name from clean first/last so the fields agree."""
    has_both = (
        is_individual &
        df['contributor_first_name'].notna() &
        df['contributor_last_name'].notna()
    )
    if has_both.any():
        rebuilt = (
            df.loc[has_both, 'contributor_last_name'].astype(str) + ', ' +
            df.loc[has_both, 'contributor_first_name'].astype(str)
        )
        current = df.loc[has_both, 'contributor_name']
        changed_mask = (current != rebuilt)
        if changed_mask.any():
            df.loc[changed_mask[changed_mask].index, 'contributor_name'] = rebuilt[changed_mask]


# (function, scope, step, reason)
NAME_STEPS = (
    (_remove_name_titles, 'individual', 'names_remove_titles', 'title_removed_from_name'),
    (_handle_multi_comma_names, 'individual', 'names_multi_comma',
     'embedded_credential_removed_and_name_split'),
    (_split_missing_names, 'individual', 'names_split_missing', 'first_last_split_from_composite_name'),
    (_clean_committee_names, 'committee', 'names_committee', 'committee_name_tail_stripped_or_curated_fix'),
    (_strip_individual_titles_suffixes, 'individual', 'names_titles_suffixes',
     'title_or_suffix_stripped_or_surname_from_email'),
    (_fix_garbled_first_names, 'individual', 'names_garbled_first', 'keyboard_error'),
    (_keep_filed_given_names, 'individual', 'names_keep_filed_given',
     'given_name_kept_where_first_name_field_dropped_it'),
    (_fix_title_as_first_name, 'individual', 'names_title_as_first', 'title_as_first_name_cleared'),
    (_fix_compound_last_names, 'individual', 'names_compound_last', 'compound_last_name_split'),
    (_rebuild_contributor_name, 'individual', 'names_rebuild', 'contributor_name_rebuilt_from_first_last'),
)


def _clean_names(df: pd.DataFrame, trail=None) -> None:
    """Clean names in place, rebuild contributor_name."""
    trail = trail or AuditTrail()
    for col in ['contributor_first_name', 'contributor_last_name']:
        df[col] = df[col].astype('object')

    masks = {
        'individual': df['entity_type'] == 'INDIVIDUAL',
        'committee': df['entity_type'] == 'COMMITTEE/PAC',
    }
    for fn, scope, step, reason in NAME_STEPS:
        def transform(d, fn=fn, mask=masks[scope]):
            return fn(d, mask)
        trail.run(df, transform, step, reason, NAME_FIELDS)
