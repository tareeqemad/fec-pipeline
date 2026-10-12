"""Split names into parts; move titles out."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.cleaning.name_rules import COMMITTEE_NAME_FIXES
from fec.config.data import (
    COMM_TAIL_RE,
    PRO_SUFFIX_RE,
    SUFFIX_RE,
    TITLE_RE,
    TITLE_TO_OCCUPATION,
)
from fec.log import get_logger

logger = get_logger(__name__)

# professional or religious title after the comma
_NAME_TITLE_RE = re.compile(
    r',\s*(DR\.?|RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|'
    r'IMAM|REV\.?|REVEREND|JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|AMB\.?|AMBASSADOR)\s',
    re.IGNORECASE,
)


_EMPTY_OCC = {np.nan, None, '', 'NOT DISCLOSED', 'NOT EMPLOYED'}


# LAST, titles or suffixes, FIRST
_EMBEDDED_TITLE_RE = re.compile(
    r'^([A-Z][A-Z\'\-]+)\s*,?\s*'
    r'(?:,?\s*(?:MD|M\.?D\.?|PHD|PH\.?D\.?|DDS|DPM|JR\.?|SR\.?|ESQ\.?|II|III|IV|FAAOS|FACS|DO|DVM)\s*)*'
    r',\s*(.+)$',
    re.IGNORECASE
)


_NAN_REPLACE = {'nan': np.nan, 'None': np.nan, '': np.nan}


# remove stray punctuation from filed names
def preclean_name_punctuation(df: pd.DataFrame) -> None:
    df['contributor_name'] = (
        df['contributor_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(r'^\.+', '', regex=True)
        # dot right after a comma is noise
        .str.replace(r',\s*\.+\s*', ', ', regex=True)
        .str.replace(r',{2,}', ',', regex=True)
        # trailing dash would leak into first_name
        .str.replace(r'\s*-\s*$', '', regex=True)
        # split glued initial; "N.A." stays intact
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


# strip name titles; fill empty occupation
def extract_title_to_occupation(df: pd.DataFrame, is_individual: pd.Series) -> None:
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


# split multi-comma titled names into last, first
def handle_multi_comma_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
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


# split LAST, FIRST when parts are missing
def split_missing_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
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
        fn_to_fill = missing_last & df['contributor_first_name'].isna()
        if fn_to_fill.any():
            df.loc[fn_to_fill, 'contributor_first_name'] = (
                split2.loc[fn_to_fill.reindex(split2.index, fill_value=False), 1].str.strip()
            )


# clear committee name parts, strip trailing junk
def clean_committee_names(df: pd.DataFrame, is_committee: pd.Series) -> None:
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


# strip titles/suffixes from names and fix email-in-last-name
def strip_individual_titles_suffixes(df: pd.DataFrame, is_individual: pd.Series) -> None:
    df.loc[is_individual, 'contributor_first_name'] = (
        df.loc[is_individual, 'contributor_first_name'].astype(str)
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(TITLE_RE, '', regex=True)
        # space a glued initial, like "W.DAVID"
        .str.replace(r'\b([A-Z])\.(?=[A-Z]{2})', r'\1. ', regex=True)
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
