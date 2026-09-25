"""One employer name per donor; organization donors named like their company."""
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.employer_synonyms import canonical_key
from fec.config.constants import EMPLOYER_STATUS_VALUES
from fec.donor_match.components import UnionFind

# legal suffixes/connectors carry no identity when comparing employer names
_EMP_DROP_TOKENS = frozenset(
    {
        "LLP",
        "LLC",
        "INC",
        "PC",
        "CO",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "LP",
        "LTD",
        "PLLC",
        "PA",
        "APC",
        "CHARTERED",
        "THE",
        "AND",
        "OF",
    }
)


_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# significant name tokens with legal suffixes removed
def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = _EMP_TOKEN_RE.findall(name.upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


# cluster near-duplicate employer names, map each to canonical
def _employer_variant_map(names: list[str]) -> dict[str, str]:
    cores = {name: _emp_core_tokens(name) for name in names}
    union = UnionFind()
    for left_index in range(len(names)):
        for right_index in range(left_index + 1, len(names)):
            left = names[left_index]
            right = names[right_index]
            left_core = cores[left]
            right_core = cores[right]
            if len(left_core & right_core) >= 2 and (
                left_core <= right_core or right_core <= left_core
            ):
                union.union(left, right)

    clusters = defaultdict(list)
    for name in names:
        clusters[union.find(name)].append(name)

    remap = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda name: (len(cores[name]), len(name)))
        remap.update({name: canonical for name in members if name != canonical})
    return remap


# rewrite each row's employer name using the variant map
def _apply_employer_variants(df: pd.DataFrame, indexes, remap: dict) -> int:
    changed = 0
    for index in indexes:
        current = df.at[index, "contributor_employer"]
        if isinstance(current, str) and current.strip() in remap:
            df.at[index, "contributor_employer"] = remap[current.strip()]
            changed += 1
    return changed


# unify employer name variants within each donor's history
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


# rename org donors to match the donor-side company spelling
def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """Rename ORGANIZATION donors to the canonical employer spelling of the same company (reuses canonical_key, adds no new normalization); returns rows aligned."""
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

    # name plus its 'last, first' swapped comma form
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


# drop legal suffix when bare name is also filed
def unify_org_donor_suffix_variants(df: pd.DataFrame) -> int:
    """ORGANIZATION donors whose name is another organization donor's name plus a trailing legal form (EATON STEEL CORPORATION next to EATON STEEL) take the suffix-free spelling; names only, donor_keys are untouched; returns rows renamed."""
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
