"""Contributor name cleaning."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.audit_trail import NAME_FIELDS, WORK_FIELDS, AuditTrail
from fec.cleaning.name_rules import FIRST_NAME_FIXES
from fec.cleaning.pipeline.name_parsing import (
    clean_committee_names,
    extract_title_to_occupation,
    handle_multi_comma_names,
    split_missing_names,
    strip_individual_titles_suffixes,
)
from fec.io import cell_text
from fec.log import get_logger

logger = get_logger(__name__)


_PURE_TITLE = {'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.', 'DR', 'DR.',
               'MD', 'M.D.', 'PHD', 'PH.D.', 'ESQ', 'ESQ.',
               'DDS', 'DVM', 'DO', 'RN', 'CPA', 'JR', 'JR.', 'SR', 'SR.',
               'II', 'III', 'IV'}


# fix first-name typos matching surname and ZIP
def _fix_garbled_first_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    if not FIRST_NAME_FIXES or 'contributor_zip' not in df.columns:
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
    n_garbled = 0
    for idx in df.index[candidates]:
        old_first = cell_text(df.at[idx, 'contributor_first_name'])
        last = cell_text(df.at[idx, 'contributor_last_name'])
        old_first_word = old_first.split()[0]
        new_first_word = FIRST_NAME_FIXES.get((last, old_first_word, zip5.at[idx]))
        if not new_first_word or not last:
            continue
        rest = old_first[len(old_first_word):].strip()
        new_first = f"{new_first_word} {rest}" if rest else new_first_word
        df.at[idx, 'contributor_first_name'] = new_first
        df.at[idx, 'contributor_name'] = f"{last}, {new_first}"
        n_garbled += 1
    if n_garbled:
        logger.info("Fixed %d garbled first names (keyboard errors)", n_garbled)


# given-name word: letters, apostrophes, hyphens
_GIVEN_WORD_RE = re.compile(r"[A-Z][A-Z'-]+")


# keep filed given names beyond first_name
def _keep_filed_given_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    last = df['contributor_last_name'].fillna('').astype(str).str.strip()
    filed = df['contributor_name'].fillna('').astype(str).str.partition(',')
    filed_last, filed_first = filed[0].str.strip(), filed[2].str.strip()
    candidates = is_individual & (first != '') & (filed_last == last) & (filed_first != first)
    for index in df.index[candidates]:
        if _adds_given_names(first.at[index], filed_first.at[index]):
            df.at[index, 'contributor_first_name'] = filed_first.at[index]


# true if filed first part adds names
def _adds_given_names(first: str, filed_first: str) -> bool:
    field_words, filed_words = first.split(), filed_first.split()
    added = filed_words[len(field_words):]
    return (
        bool(added)
        and filed_words[:len(field_words)] == field_words
        and all(
            _GIVEN_WORD_RE.fullmatch(word)
            and word not in _PURE_TITLE
            and word not in field_words  # a repeated word adds nothing
            for word in added
        )
    )


# clear first_name when it is a title
def _fix_title_as_first_name(df: pd.DataFrame, is_individual: pd.Series) -> None:
    title_as_first = (
        is_individual &
        df['contributor_first_name'].fillna('').str.upper().isin(_PURE_TITLE)
    )
    if title_as_first.any():
        df.loc[title_as_first, 'contributor_first_name'] = np.nan


# split two-word last name into first, last
def _fix_compound_last_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    compound_last = (
        is_individual &
        df['contributor_first_name'].isna() &
        df['contributor_last_name'].fillna('').str.contains(' ', regex=False)
    )
    if compound_last.any():
        for idx in df.loc[compound_last].index:
            last_name = str(df.at[idx, 'contributor_last_name'])
            parts = last_name.strip().split()
            if len(parts) == 2:
                df.at[idx, 'contributor_first_name'] = parts[0]
                df.at[idx, 'contributor_last_name'] = parts[1]
                df.at[idx, 'contributor_name'] = f"{parts[1]}, {parts[0]}"


# rebuild contributor_name from clean first, last
def _rebuild_contributor_name(df: pd.DataFrame, is_individual: pd.Series) -> None:
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
        n_rebuilt = int(changed_mask.sum())
        if n_rebuilt:
            df.loc[changed_mask[changed_mask].index, 'contributor_name'] = rebuilt[changed_mask]
            logger.info("Rebuilt %d contributor_name values from clean first/last", n_rebuilt)


# (function, scope, step, reason)
NAME_STEPS = (
    (extract_title_to_occupation, 'individual', 'names_title_to_occupation',
     'title_removed_from_name_or_occupation_enriched_from_it'),
    (handle_multi_comma_names, 'individual', 'names_multi_comma',
     'embedded_credential_removed_and_name_split'),
    (split_missing_names, 'individual', 'names_split_missing', 'first_last_split_from_composite_name'),
    (clean_committee_names, 'committee', 'names_committee', 'committee_name_tail_stripped_or_curated_fix'),
    (strip_individual_titles_suffixes, 'individual', 'names_titles_suffixes',
     'title_or_suffix_stripped_or_surname_from_email'),
    (_fix_garbled_first_names, 'individual', 'names_garbled_first', 'keyboard_error'),
    (_keep_filed_given_names, 'individual', 'names_keep_filed_given',
     'given_name_kept_where_first_name_field_dropped_it'),
    (_fix_title_as_first_name, 'individual', 'names_title_as_first', 'title_as_first_name_cleared'),
    (_fix_compound_last_names, 'individual', 'names_compound_last', 'compound_last_name_split'),
    (_rebuild_contributor_name, 'individual', 'names_rebuild', 'contributor_name_rebuilt_from_first_last'),
)


# run every name step in place, audited
def clean_names(df: pd.DataFrame, trail=None) -> None:
    trail = trail or AuditTrail()
    for col in ['contributor_first_name', 'contributor_last_name']:
        df[col] = df[col].astype('object')

    masks = {
        'individual': df['entity_type'] == 'INDIVIDUAL',
        'committee': df['entity_type'] == 'COMMITTEE/PAC',
    }
    for fn, scope, step, reason in NAME_STEPS:
        transform = lambda d, fn=fn, mask=masks[scope]: fn(d, mask)
        trail.run(df, transform, step, reason, NAME_FIELDS + WORK_FIELDS)
