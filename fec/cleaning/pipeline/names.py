"""Contributor name cleaning."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from fec.config.data import (
    TITLE_TO_OCCUPATION, COMM_TAIL_RE,
    TITLE_RE, SUFFIX_RE, PRO_SUFFIX_RE,
)
from fec.cleaning.name_rules import COMMITTEE_NAME_FIXES, FIRST_NAME_FIXES
from fec.log import get_logger

logger = get_logger(__name__)

# professional/religious titles appearing after the comma in a name
_NAME_TITLE_RE = re.compile(
    r',\s*(DR\.?|RABBI|CANTOR|PASTOR|DEACON|BISHOP|FATHER|SISTER|'
    r'IMAM|REV\.?|REVEREND|JUDGE|HON\.?|HONORABLE|'
    r'PROF\.?|PROFESSOR|AMB\.?|AMBASSADOR)\s',
    re.IGNORECASE,
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
    df['contributor_first_name'] = (
        df['contributor_first_name'].astype('string')
        .str.replace('`', '', regex=False)
        .str.replace(';', '', regex=False)
        .str.replace(r'^\.+\s*', '', regex=True)
        .str.strip()
        .replace('', pd.NA)
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


def _text(value) -> str:
    """A name cell as text; NaN / None / pd.NA (all truthy or ambiguous) become ''."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ''
    return str(value).strip()


def _fix_garbled_first_names(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Fix a verified keyboard error in ONE filer's first name (FISHER, JEFREY -> JEFFREY).

    Each first_name rule in contributor_name_rules.csv was checked against that
    filer's own other filings and names the filer it was written for: the
    surname, the garbled first word and the ZIP5 of the address. A row is
    corrected only when all three match, so the same word on anyone else's
    filing (BRIA, CAROLL, AURI, DORUS and ISSAC are real given names) is left
    as filed, and a row without a surname or ZIP is never touched.
    """
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
        n_garbled += 1
    if n_garbled:
        logger.info("Fixed %d garbled first names (keyboard errors)", n_garbled)


def _keep_given_name_after_initial(df: pd.DataFrame, is_individual: pd.Series) -> None:
    """Keep a filed given name the first-name field cut to an initial.

    FEC's first-name field for "HARRIS, S. WOLF" is only "S."; rebuilding the
    name from it would drop WOLF. When the filed name is the surname, that
    initial and one more word, the first name takes the filed form.
    """
    first = df['contributor_first_name'].fillna('').astype(str).str.strip()
    last = df['contributor_last_name'].fillna('').astype(str).str.strip()
    filed_last, _, filed_first = (
        df['contributor_name'].fillna('').astype(str).str.partition(',').T.values
    )
    filed_first = pd.Series(filed_first, index=df.index).str.strip()
    initial_only = first.str.fullmatch(r'[A-Z]\.?')
    kept = (
        is_individual
        & initial_only
        & (pd.Series(filed_last, index=df.index).str.strip() == last)
        & filed_first.str.fullmatch(r"[A-Z]\.?\s+[A-Z][A-Z'-]+")
        & (filed_first.str[0] == first.str[0])
    )
    df.loc[kept, 'contributor_first_name'] = filed_first[kept]


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


# (function, scope, step, reason)
NAME_STEPS = (
    (_extract_title_to_occupation, 'individual', 'names_title_to_occupation',
     'title_removed_from_name_or_occupation_enriched_from_it'),
    (_handle_multi_comma_names, 'individual', 'names_multi_comma',
     'embedded_credential_removed_and_name_split'),
    (_split_missing_names, 'individual', 'names_split_missing', 'first_last_split_from_composite_name'),
    (_clean_committee_names, 'committee', 'names_committee', 'committee_name_tail_stripped_or_curated_fix'),
    (_strip_individual_titles_suffixes, 'individual', 'names_titles_suffixes',
     'title_or_suffix_stripped_or_surname_from_email'),
    (_fix_garbled_first_names, 'individual', 'names_garbled_first', 'keyboard_error'),
    (_keep_given_name_after_initial, 'individual', 'names_given_after_initial',
     'given_name_kept_where_first_name_field_had_only_the_initial'),
    (_fix_title_as_first_name, 'individual', 'names_title_as_first', 'title_as_first_name_cleared'),
    (_fix_compound_last_names, 'individual', 'names_compound_last', 'compound_last_name_split'),
    (_rebuild_contributor_name, 'individual', 'names_rebuild', 'contributor_name_rebuilt_from_first_last'),
)


def _clean_names(df: pd.DataFrame, trail=None) -> None:
    """Clean names in place, rebuild contributor_name."""
    from fec.cleaning.audit_trail import NAME_FIELDS, WORK_FIELDS, AuditTrail

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
