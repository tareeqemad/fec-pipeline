"""Unify one donor's employer typos, truncations and acronyms."""
import re
from difflib import SequenceMatcher

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.config.constants import (
    SKIP_EMPLOYERS,
)


# yield each donor's real employer names and counts
def _donor_employer_groups(df: pd.DataFrame):
    """Yield (donor_key, real employers, counts) per donor."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in SKIP_EMPLOYERS]
        if len(real) < 2:
            continue
        yield dk, real, grp['contributor_employer'].value_counts()


# converge near-identical employer spellings for the same donor
def _employer_typos(df: pd.DataFrame) -> int:
    """Same donor: near-identical employer spellings converge."""
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        canonical = max(real, key=lambda e: counts[e])
        cc = counts[canonical]

        for other in real:
            if other == canonical:
                continue
            oc = counts[other]
            if oc >= cc:
                continue
            other_u, canonical_u = other.upper(), canonical.upper()
            lev_ok = (levenshtein(other_u, canonical_u) <= 2
                      and cc / oc >= 3)
            fuzzy_ok = SequenceMatcher(
                None, other_u, canonical_u).ratio() * 100 >= 90
            if lev_ok or fuzzy_ok:
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == other)
                df.loc[mask, 'contributor_employer'] = canonical
                n_fixed += int(mask.sum())
    return n_fixed


_PARENT_BRAND_MIN_DONORS = 5     # a short name that this many people file on its own is a real parent (NYU), not a truncation


_ACRONYM_STOP = {'OF', 'THE', 'AND', 'FOR', 'IN', 'AT', 'DE', 'LA', 'LLC', 'INC', 'LLP', 'CORP', 'LTD', 'PC', 'LP', 'PLLC'}


# count distinct donors who file each employer name alone
def _standalone_donor_counts(df: pd.DataFrame) -> dict:
    """employer -> number of distinct donors who file exactly that name."""
    indiv = df[(df['entity_type'] == 'INDIVIDUAL') & df['contributor_employer'].notna()]
    return indiv.groupby('contributor_employer')['donor_key'].nunique().to_dict()


# check whether short appears as a whole word inside long_
def _contains_as_words(short: str, long_: str) -> bool:
    return f' {short.upper()} ' in f' {long_.upper()} '


# merge same-donor employer names, one a substring of the other
def _employer_substring_variants(df: pd.DataFrame) -> int:
    """Same donor, one name inside the other as whole words (SYNERGY / SYNERGY HEALTH PARTNERS): one company.

    The full name wins because the short one is a truncation of it, except when the short
    name is a parent brand that many people file on its own (NYU next to NYU LANGONE HEALTH
    HUNTINGTON MEDICAL): then the division folds into the parent, as the employer rules do."""
    standalone = _standalone_donor_counts(df)
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        fixed_in_group = set()
        for i, a in enumerate(real):
            for b in real[i + 1:]:
                if a in fixed_in_group or b in fixed_in_group:
                    continue
                if _contains_as_words(a, b):
                    short, long_ = a, b
                elif _contains_as_words(b, a):
                    short, long_ = b, a
                else:
                    continue
                winner = short if standalone.get(short, 0) >= _PARENT_BRAND_MIN_DONORS else long_
                loser = long_ if winner == short else short
                mask = (df['donor_key'] == dk) & (df['contributor_employer'] == loser)
                n = int(mask.sum())
                if n:
                    df.loc[mask, 'contributor_employer'] = winner
                    fixed_in_group.add(loser)
                    n_fixed += n
    return n_fixed


# build acronyms of a name, with and without stopwords
def _acronym_of(name: str) -> tuple[str, str]:
    tokens = re.findall(r'[A-Z0-9&]+', name.upper())
    return (''.join(t[0] for t in tokens if t not in _ACRONYM_STOP),
            ''.join(t[0] for t in tokens))


# replace a donor's employer acronym with the matching full name
def _employer_acronym_variants(df: pd.DataFrame) -> int:
    """Same donor, an acronym next to the name it abbreviates (WPCM / WHITE PINE CAPITAL MANAGEMENT): the full name wins."""
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        longs = [e for e in real if len(re.findall(r'[A-Z0-9&]+', e.upper())) >= 2]
        for short in real:
            s = re.sub(r'[^A-Z0-9&]', '', short.upper())
            if not (2 <= len(s) <= 6) or ' ' in short.strip() and len(short.split()) > 2:
                continue
            for long_ in longs:
                if long_ == short or len(long_) <= len(short):
                    continue
                if s in _acronym_of(long_):
                    mask = (df['donor_key'] == dk) & (df['contributor_employer'] == short)
                    n = int(mask.sum())
                    if n:
                        df.loc[mask, 'contributor_employer'] = long_
                        n_fixed += n
                    break
    return n_fixed
