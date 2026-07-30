"""Canonical grouping key, cross-column re-canonicalization, and fuzzy typo merging."""
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from fec.cleaning._helpers import _indiv_idx, _norm
from fec.log import get_logger

logger = get_logger(__name__)

# COMPANY listed before CO (and INCORPORATED before INC) so iteration strips
# the longer suffix first - otherwise "MPANY" residue would be left behind.
_CORP_SUFFIXES = ('INCORPORATED', 'CORPORATION', 'COMPANY', 'ENTERPRISES',
                  'PLLC', 'CORP', 'GROUP', 'LLC', 'LLP', 'INC', 'LTD', 'CO',
                  'PC', 'PA', 'LP')

# Whole-token abbreviations folded for grouping only (never display).
# ASSOC deliberately absent: ASSOCIATES vs ASSOCIATION is contextual.
_KEY_TOKEN_EXPANSIONS = {'UNIV': 'UNIVERSITY', 'MT': 'MOUNT',
                         'ASSOCS': 'ASSOCIATES'}


def canonical_key(name: str) -> str:
    """Strict grouping key: whitespace/punctuation/TLD/legal-suffix/THE/ampersand variants collapse to one key (KIRKLAND & ELLIS LLP -> KIRKLANDELLIS)."""
    key = name.strip().upper()
    if not key:
        return ''
    key = re.sub(r'\.(COM|ORG|NET|IO|AI|US|EDU|GOV)\b', '', key)
    # `&`/`$` -> AND, then drop the word AND entirely for grouping.
    # Word-boundary substitutions must run BEFORE collapsing whitespace,
    # otherwise the AND inside KIRKLAND would be eaten.
    key = re.sub(r'[&$]', ' AND ', key)
    key = re.sub(r'^\s*THE\b', '', key)
    key = re.sub(r'\bAND\b', ' ', key)
    key = ' '.join(_KEY_TOKEN_EXPANSIONS.get(token, token) for token in key.split())
    key = re.sub(r'[^A-Z0-9]+', '', key)
    # iteratively strip trailing corporate suffixes until stable
    while True:
        for suffix in _CORP_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix) + 3:
                key = key[:-len(suffix)]
                break
        else:
            break
    return key


def restore_display_suffixes(df: pd.DataFrame, raw_csv_path) -> int:
    """End-of-cleaning pass: restore each employer's most-common raw suffix-bearing form (per canonical_key) so the saved CSV reads naturally; idempotent."""
    if not Path(raw_csv_path).exists():
        return 0

    raw = pd.read_csv(raw_csv_path, usecols=['contributor_employer'],
                      dtype=str, keep_default_na=False, low_memory=False)
    # uppercase, collapse whitespace and `+`, drop stray trailing dot tails
    # so the restored display form carries no FEC keying junk
    raw['emp'] = (raw['contributor_employer'].str.strip().str.upper()
                  .str.replace(r'[\s+]+', ' ', regex=True)
                  .str.replace(r'(?:\s*\.)+\s*$', '', regex=True)
                  .str.strip())
    raw = raw[raw['emp'] != '']
    raw['key'] = raw['emp'].map(canonical_key)
    raw = raw[raw['key'] != '']

    if raw.empty:
        return 0

    most_common_form = (
        raw.groupby('key')['emp']
        .agg(lambda values: values.mode().iloc[0])
        .to_dict()
    )

    indiv_idx = _indiv_idx(df)
    current = _norm(df.loc[indiv_idx, 'contributor_employer'])
    has_emp = current != ''
    target = indiv_idx[has_emp]
    if len(target) == 0:
        return 0

    current_clean = current[has_emp]
    keys = current_clean.map(canonical_key)
    new_values = keys.map(most_common_form).fillna(current_clean)

    changed_mask = new_values != current_clean
    n_changed = int(changed_mask.sum())
    if n_changed:
        df.loc[target[changed_mask], 'contributor_employer'] = new_values[changed_mask]
    return n_changed


def _recanonicalize_employers(df: pd.DataFrame) -> int:
    """Final re-canonicalization after enhancements create new variant groups; returns rows changed."""
    indiv_idx = _indiv_idx(df)
    # Frequencies span BOTH contributor_employer AND previous_employer so a
    # retiree-only variant merges under the same canonical as the current
    # employer's spelling - otherwise the loader builds two employers rows
    # for the same firm.
    cols = [column for column in ('contributor_employer', 'previous_employer') if column in df.columns]
    series = []
    for column in cols:
        values = df.loc[indiv_idx, column]
        values = values[_norm(values) != '']
        series.append(values)
    if not series:
        return 0
    all_names = pd.concat(series, ignore_index=True)
    counts = all_names.value_counts()
    if counts.empty:
        return 0

    groups = defaultdict(list)
    for name in counts.index:
        key = canonical_key(name)
        if key:
            groups[key].append(name)

    mapping = {}
    for key, variants in groups.items():
        if len(variants) < 2:
            continue
        canonical = sorted(variants,
                           key=lambda variant: (-counts.get(variant, 0), -len(variant), variant))[0]
        for variant in variants:
            if variant != canonical:
                mapping[variant] = canonical

    if not mapping:
        return 0

    n_changed = 0
    for column in cols:
        to_fix = df[column].isin(mapping)
        n_col = int(to_fix.sum())
        if n_col:
            df.loc[to_fix, column] = df.loc[to_fix, column].map(mapping)
            n_changed += n_col

    return n_changed


def merge_typo_variants(df: pd.DataFrame, threshold: int = 92) -> int:
    """Merge employer names that are typos of each other (rapidfuzz token_sort_ratio, bucketed by first 4 alphanumerics); winner = most-frequent variant; returns rows changed."""
    try:
        from rapidfuzz import fuzz
    except ImportError:
        logger.warning("    rapidfuzz not installed - skipping typo merge")
        return 0

    indiv_idx = _indiv_idx(df)
    # count frequencies across BOTH employer columns so retiree-only names
    # are weighed too
    cols = [column for column in ('contributor_employer', 'previous_employer') if column in df.columns]
    series_list = []
    for column in cols:
        values = df.loc[indiv_idx, column].astype(str).str.strip().str.upper()
        values = values[(values != '') & (values != 'NAN')]
        series_list.append(values)
    if not series_list:
        return 0
    all_names = pd.concat(series_list, ignore_index=True)
    counts = all_names.value_counts()
    if counts.empty:
        return 0
    names = list(counts.index)

    buckets: dict[str, list[str]] = {}
    for name in names:
        key = re.sub(r'[^A-Z0-9]', '', name)[:4]
        if not key:
            continue
        buckets.setdefault(key, []).append(name)

    # union-find so that A-B and B-C cluster A,B,C together
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        parent.setdefault(a, a)
        parent.setdefault(b, b)
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_a] = root_b

    for bucket_names in buckets.values():
        if len(bucket_names) < 2:
            continue
        for index_a in range(len(bucket_names)):
            for index_b in range(index_a + 1, len(bucket_names)):
                name_a, name_b = bucket_names[index_a], bucket_names[index_b]
                if fuzz.token_sort_ratio(name_a, name_b) >= threshold:
                    union(name_a, name_b)

    # canonical = most-frequent in cluster; tie-break longer name, then alpha
    clusters: dict[str, list[str]] = {}
    for name in parent:
        clusters.setdefault(find(name), []).append(name)

    mapping = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = sorted(
            members,
            key=lambda variant: (-counts.get(variant, 0), -len(variant), variant)
        )[0]
        for member in members:
            if member != canonical:
                mapping[member] = canonical

    if not mapping:
        return 0

    n_changed = 0
    # apply to BOTH columns so previous_employer typos also merge
    for col in ('contributor_employer', 'previous_employer'):
        if col not in df.columns:
            continue
        col_upper = df[col].astype(str).str.strip().str.upper()
        to_fix = col_upper.isin(mapping)
        n_col = int(to_fix.sum())
        if n_col:
            df.loc[to_fix, col] = col_upper[to_fix].map(mapping)
            n_changed += n_col
    return n_changed
