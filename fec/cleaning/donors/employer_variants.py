"""Unify one donor's employer typos, truncations and acronyms."""
import re
from collections import defaultdict
from difflib import SequenceMatcher

import pandas as pd

from fec.cleaning._helpers import levenshtein
from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import preserved_employer_name
from fec.config.constants import (
    EMPLOYER_STATUS_VALUES,
    SKIP_EMPLOYERS,
)


def _donor_employer_groups(df: pd.DataFrame):
    """Yield (donor_key, real employers, counts) per donor."""
    indiv = df[df['entity_type'] == 'INDIVIDUAL']
    for dk, grp in indiv.groupby('donor_key'):
        emps = grp['contributor_employer'].dropna().unique()
        real = [e for e in emps if e not in SKIP_EMPLOYERS and not preserved_employer_name(e)]
        if len(real) < 2:
            continue
        yield dk, real, grp['contributor_employer'].value_counts()


def _joined_employer_component(single: str, combined: str) -> bool:
    """Keep standalone employers separate from filings naming multiple affiliations."""
    single = canonical_key(single)
    if not single:
        return False
    combined = ' '.join(combined.upper().split())
    for join in re.finditer(r'\s+(?:AND|&)\s+|[/;]', combined):
        left = canonical_key(combined[:join.start()])
        right = canonical_key(combined[join.end():])
        if left and right and left != right and single in (left, right):
            return True
    parts = [canonical_key(part) for part in re.split(r'[/;]', combined)]
    if single in parts and any(part and part != single for part in parts):
        return True
    return False


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
            if (_joined_employer_component(other, canonical)
                    or _joined_employer_component(canonical, other)):
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


def _standalone_donor_counts(df: pd.DataFrame) -> dict:
    """employer -> number of distinct donors who file exactly that name."""
    indiv = df[(df['entity_type'] == 'INDIVIDUAL') & df['contributor_employer'].notna()]
    return indiv.groupby('contributor_employer')['donor_key'].nunique().to_dict()


def _contains_as_words(short: str, long_: str) -> bool:
    return f' {short.upper()} ' in f' {long_.upper()} '


def _employer_substring_variants(df: pd.DataFrame) -> int:
    """Prefer the fuller employer name, except for established parent brands; preserve combined affiliations."""
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
                if _joined_employer_component(short, long_):
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


def _acronym_of(name: str) -> tuple[str, str]:
    tokens = re.findall(r'[A-Z0-9&]+', name.upper())
    return (''.join(t[0] for t in tokens if t not in _ACRONYM_STOP),
            ''.join(t[0] for t in tokens))


def _employer_acronym_variants(df: pd.DataFrame) -> int:
    """Same donor, an acronym next to the name it abbreviates (WPCM / WHITE PINE CAPITAL MANAGEMENT): the full name wins."""
    n_fixed = 0
    for dk, real, counts in _donor_employer_groups(df):
        longs = [
            (employer, _acronym_of(employer))
            for employer in real
            if len(re.findall(r'[A-Z0-9&]+', employer.upper())) >= 2
        ]
        for short in real:
            s = re.sub(r'[^A-Z0-9&]', '', short.upper())
            if not (2 <= len(s) <= 6) or ' ' in short.strip() and len(short.split()) > 2:
                continue
            for long_, acronyms in longs:
                if long_ == short or len(long_) <= len(short):
                    continue
                if s in acronyms:
                    mask = (df['donor_key'] == dk) & (df['contributor_employer'] == short)
                    n = int(mask.sum())
                    if n:
                        df.loc[mask, 'contributor_employer'] = long_
                        n_fixed += n
                    break
    return n_fixed


# legal suffixes/connectors carry no identity when comparing employer names
_EMP_DROP_TOKENS = frozenset({
    "LLP", "LLC", "INC", "PC", "CO", "CORP", "CORPORATION", "COMPANY",
    "LP", "LTD", "PLLC", "PA", "APC", "CHARTERED", "THE", "AND", "OF",
})


# a run of letters/digits, to tokenize an employer name
_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = _EMP_TOKEN_RE.findall(name.upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


def _employer_variant_map(names: list[str]) -> dict[str, str]:
    """Unify equivalent names or a uniquely fuller name; keep ambiguous affiliations separate."""
    cores = {name: _emp_core_tokens(name) for name in names}
    by_core = defaultdict(list)
    for name in names:
        by_core[cores[name]].append(name)
    # one display name per word set: the longest spelling
    display = {core: max(members, key=len) for core, members in by_core.items()}

    remap = {}
    for core, members in by_core.items():
        fuller = [
            other for other in by_core
            if other > core and len(core & other) >= 2
            and not any(
                _joined_employer_component(name, display[other]) for name in members
            )
        ]
        widest = [other for other in fuller if not any(other < more for more in fuller)]
        target = display[widest[0]] if len(widest) == 1 else display[core]
        if not preserved_employer_name(target):
            remap.update({name: target for name in members
                          if name != target and not preserved_employer_name(name)})
    return remap


def _apply_employer_variants(df: pd.DataFrame, indexes, remap: dict) -> int:
    changed = 0
    for index in indexes:
        current = df.at[index, "contributor_employer"]
        if isinstance(current, str) and current.strip() in remap:
            df.at[index, "contributor_employer"] = remap[current.strip()]
            changed += 1
    return changed


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    """Unify clear employer variants within each donor's history."""
    individuals = df["entity_type"] == "INDIVIDUAL"
    if not individuals.any():
        return 0

    changed = 0
    for indexes in df[individuals].groupby("donor_key").groups.values():
        values = df.loc[indexes, "contributor_employer"].dropna().map(str).str.strip()
        names = [
            name
            for name in values.unique()
            if name and name.upper() not in EMPLOYER_STATUS_VALUES
        ]
        if len(names) >= 2:
            remap = _employer_variant_map(names)
            if remap:
                changed += _apply_employer_variants(df, indexes, remap)
    return changed


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """Align organization names with the most filed employer spelling of the same company."""
    # canonical display name per canonical_key = the donor-side spelling seen most
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    emp = ind["contributor_employer"].dropna().astype(str).str.strip()
    emp = emp[(emp != "") & (~emp.str.upper().isin(EMPLOYER_STATUS_VALUES))]
    if emp.empty:
        return 0
    by_key: dict[str, str] = {}
    for name in emp.value_counts().index:  # value_counts: most common first
        k = canonical_key(name)
        if k and k not in by_key:
            by_key[k] = name

    def _forms(n: str):
        out = [n]
        if "," in n:  # "CAPITAL, WHITE" -> "WHITE CAPITAL"
            a, b = n.split(",", 1)
            out.append(f"{b.strip()} {a.strip()}")
        return out

    org_names = (
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"]
        .dropna()
        .unique()
    )
    remap: dict[str, str] = {}
    for nm in org_names:
        for form in _forms(str(nm)):
            k = canonical_key(form)
            if k and k in by_key and by_key[k].upper() != str(nm).upper():
                remap[nm] = by_key[k]
                break
    if not remap:
        return 0
    mask = (df["entity_type"] == "ORGANIZATION") & df["contributor_name"].isin(remap)
    df.loc[mask, "contributor_name"] = df.loc[mask, "contributor_name"].map(remap)
    return int(mask.sum())


# trailing legal form of a company name ("EATON STEEL CORPORATION" -> "EATON STEEL")
_ORG_LEGAL_TAIL_RE = re.compile(
    r"(?:[\s,]+(?:CORPORATION|CORP|INCORPORATED|INC|COMPANY|CO|LLC|LLP|LTD|LP|PLLC)\.?)+$"
)


def unify_org_donor_suffix_variants(df: pd.DataFrame) -> int:
    """Drop an organization suffix only when the bare name is also filed; keep donor keys."""
    org = df["entity_type"] == "ORGANIZATION"
    if not org.any():
        return 0
    names = df.loc[org, "contributor_name"].dropna().astype(str).str.strip()
    distinct = set(names[names != ""])
    remap = {}
    for name in distinct:
        bare = _ORG_LEGAL_TAIL_RE.sub("", name).strip()
        # the bare spelling must itself be filed as an organization donor:
        # only then is it provably the same name, not a guessed short form
        if bare and bare != name and bare in distinct:
            remap[name] = bare
    if not remap:
        return 0
    current = df["contributor_name"].where(df["contributor_name"].notna(), "").astype(str).str.strip()
    mask = org & current.isin(remap)
    df.loc[mask, "contributor_name"] = current[mask].map(remap)
    return int(mask.sum())
