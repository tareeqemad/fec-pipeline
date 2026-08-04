"""Contributor-name cleaning: parse, tidy, and keep contributor_name in sync with first/last."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config import (
    TITLE_TO_OCCUPATION, COMM_TAIL_RE, COMMITTEE_NAME_FIXES,
    TITLE_RE, SUFFIX_RE, PRO_SUFFIX_RE, FIRST_NAME_FIXES,
)
from fec.log import get_logger

logger = get_logger(__name__)

# professional/religious titles appearing after the comma in a name
_NAME_TITLE_RE = re.compile(
    r',\s*(DR\.?|RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|'
    r'IMAM|REV\.?|REVEREND|JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|AMB\.?|AMBASSADOR)\s',
    re.I,
)

_EMPTY_OCC = {np.nan, None, '', 'NOT DISCLOSED', 'NOT EMPLOYED'}

# LAST, <titles/suffixes>, FIRST: WIENIR, MD, MICHAEL
_EMBEDDED_TITLE_RE = re.compile(
    r'^([A-Z][A-Z\'\-]+)\s*,?\s*'
    r'(?:,?\s*(?:MD|M\.?D\.?|PHD|PH\.?D\.?|DDS|DPM|JR\.?|SR\.?|ESQ\.?|II|III|IV|FAAOS|FACS|DO|DVM)\s*)*'
    r',\s*(.+)$',
    re.IGNORECASE
)

_PURE_TITLE = {'MR', 'MR.', 'MRS', 'MRS.', 'MS', 'MS.', 'DR', 'DR.',
               'MD', 'M.D.', 'PHD', 'PH.D.', 'ESQ', 'ESQ.',
               'DDS', 'DVM', 'JR', 'JR.', 'SR', 'SR.'}

_NAN_REPLACE = {'nan': np.nan, 'None': np.nan, '': np.nan}


def _clean_names(df: pd.DataFrame) -> None:
    """Clean contributor names in-place: punctuation, titles, LAST/FIRST splits, committee tails, garbled fixes, then rebuild contributor_name."""
    for col in ['contributor_first_name', 'contributor_last_name']:
        df[col] = df[col].astype('object')

    is_individual = df['entity_type'] == 'INDIVIDUAL'
    is_committee = df['entity_type'] == 'COMMITTEE/PAC'

    _preclean_name_punctuation(df)
    _extract_title_to_occupation(df, is_individual)
    _handle_multi_comma_names(df, is_individual)
    _split_missing_names(df, is_individual)
    _clean_committee_names(df, is_committee)
    _strip_individual_titles_suffixes(df, is_individual)
    _fix_garbled_first_names(df, is_individual)
    _fix_title_as_first_name(df, is_individual)
    _fix_compound_last_names(df, is_individual)
    _rebuild_contributor_name(df, is_individual)


def _preclean_name_punctuation(df: pd.DataFrame) -> None:
    """Remove backticks, semicolons, stray dots, collapse double commas."""
    df['contributor_name'] = (
        df['contributor_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(r'^\.+', '', regex=True)
        # dot directly after the comma ("HAAS, .CANDICE") is filer noise; the
        # dot must IMMEDIATELY follow the comma so dotted tails ("N.A.") survive
        .str.replace(r',\s*\.+\s*', ', ', regex=True)
        .str.replace(r',{2,}', ',', regex=True)
        # trailing dash ("LEVY, ALLAN -") would leak into first_name
        .str.replace(r'\s*-\s*$', '', regex=True)
        # glued initial ("W.DAVID" -> "W DAVID"); second part needs 2+ letters
        # so dotted abbreviations like "N.A." and "J.P." stay intact
        .str.replace(r'\b([A-Z])\.([A-Z]{2,})', r'\1 \2', regex=True)
        .str.strip()
        .str.replace(r'\s+', ' ', regex=True)
    )


def _extract_title_to_occupation(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Extract professional/religious titles from the name; enrich occupation if empty."""
    n_title_enriched = 0

    for idx in df.loc[is_individual].index:
        name = str(df.at[idx, 'contributor_name'])
        match = _NAME_TITLE_RE.search(name)
        if not match:
            continue

        title_raw = match.group(1).upper().rstrip('.')
        clean_name = name[:match.start(1)] + name[match.end(1):]
        clean_name = re.sub(r',\s*,', ',', clean_name)
        clean_name = re.sub(r'\s{2,}', ' ', clean_name).strip()
        df.at[idx, 'contributor_name'] = clean_name

        occ = df.at[idx, 'contributor_occupation']
        if occ in _EMPTY_OCC or (isinstance(occ, float) and pd.isna(occ)):
            mapping = TITLE_TO_OCCUPATION.get(title_raw)
            if mapping:
                occ_val, cat_val = mapping
                df.at[idx, 'contributor_occupation'] = occ_val
                df.at[idx, 'occupation_category'] = cat_val
                n_title_enriched += 1

    if n_title_enriched:
        logger.info("Enriched %d occupations from name titles (DR->DOCTOR, RABBI->RABBI, etc.)", n_title_enriched)


def _handle_multi_comma_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Handle multi-comma names with embedded titles: WIENIR, MD, MICHAEL -> last=WIENIR, first=MICHAEL."""
    multi_comma_mask = is_individual & (df['contributor_name'].str.count(',') > 1)
    if multi_comma_mask.any():
        for idx in df.loc[multi_comma_mask].index:
            name = df.at[idx, 'contributor_name']
            match = _EMBEDDED_TITLE_RE.match(name)
            if match:
                clean_last = match.group(1).strip()
                clean_first = match.group(2).strip().rstrip('.')
                df.at[idx, 'contributor_last_name'] = clean_last
                df.at[idx, 'contributor_first_name'] = clean_first
                df.at[idx, 'contributor_name'] = f"{clean_last}, {clean_first}"


def _split_missing_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Split LAST, FIRST for reclassified records missing first/last; also repair missing last_name."""
    needs_split = is_individual & df['contributor_first_name'].isna()
    if needs_split.any():
        names = df.loc[needs_split, 'contributor_name'].astype(str)
        has_comma = names.str.contains(',', na=False)
        comma_rows = needs_split & has_comma.reindex(needs_split.index, fill_value=False)

        if comma_rows.any():
            split = df.loc[comma_rows, 'contributor_name'].str.split(',', n=1, expand=True)
            df.loc[comma_rows, 'contributor_last_name'] = split[0].str.strip()
            df.loc[comma_rows, 'contributor_first_name'] = split[1].str.strip()

    missing_last = (
        is_individual
        & df['contributor_last_name'].isna()
        & df['contributor_name'].str.contains(',', na=False)
    )
    if missing_last.any():
        split2 = df.loc[missing_last, 'contributor_name'].str.split(',', n=1, expand=True)
        df.loc[missing_last, 'contributor_last_name'] = split2[0].str.strip()
        fn_to_fill = missing_last & df['contributor_first_name'].isna()
        if fn_to_fill.any():
            df.loc[fn_to_fill, 'contributor_first_name'] = (
                split2.loc[fn_to_fill.reindex(split2.index, fill_value=False), 1].str.strip()
            )


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
    """Strip titles from first_name, suffixes from last_name, handle email in last_name."""
    df.loc[is_individual, 'contributor_first_name'] = (
        df.loc[is_individual, 'contributor_first_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(TITLE_RE, '', regex=True)
        .str.strip()
        .replace(_NAN_REPLACE)
    )

    email_in_last = (
        is_individual &
        df['contributor_last_name'].fillna('').str.contains('@', regex=False)
    )
    if email_in_last.any():
        for idx in df.loc[email_in_last].index:
            full_name = df.at[idx, 'contributor_name']
            if ',' in str(full_name):
                parts = str(full_name).split(',', 1)
                last_part = parts[0].strip()
                if '@' not in last_part:
                    df.at[idx, 'contributor_last_name'] = last_part
                else:
                    email_str = df.at[idx, 'contributor_last_name']
                    prefix = str(email_str).split('@')[0].upper()
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


def _fix_garbled_first_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Fix keyboard errors like JEFREY/ROBERTB (verified by address matching)."""
    first_names = df.loc[is_individual, 'contributor_first_name'].fillna('')
    first_word = first_names.str.split().str[0].fillna('')
    garbled_mask = is_individual & first_word.isin(FIRST_NAME_FIXES).reindex(df.index, fill_value=False)
    if garbled_mask.any():
        n_garbled = int(garbled_mask.sum())
        if '_garbled_before' not in df.columns:
            df['_garbled_before'] = pd.Series(pd.NA, index=df.index, dtype='object')
        for idx in df.loc[garbled_mask].index:
            old_first = df.at[idx, 'contributor_first_name']
            old_first_word = old_first.split()[0]
            new_first_word = FIRST_NAME_FIXES[old_first_word]
            df.at[idx, '_garbled_before'] = old_first_word
            rest = old_first[len(old_first_word):].strip()
            new_first = f"{new_first_word} {rest}".strip() if rest else new_first_word
            last = df.at[idx, 'contributor_last_name'] or ''
            df.at[idx, 'contributor_first_name'] = new_first
            df.at[idx, 'contributor_name'] = f"{last}, {new_first}"
        logger.info("Fixed %d garbled first names (keyboard errors)", n_garbled)


def _fix_title_as_first_name(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Clear first_name when it is actually a title (MRS, DR., MD, etc.)."""
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
    if compound_last.any():
        for idx in df.loc[compound_last].index:
            last_name = str(df.at[idx, 'contributor_last_name'])
            parts = last_name.strip().split()
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
        n_rebuilt = int(changed_mask.sum())
        if n_rebuilt:
            df.loc[changed_mask[changed_mask].index, 'contributor_name'] = rebuilt[changed_mask]
            logger.info("Rebuilt %d contributor_name values from clean first/last", n_rebuilt)
