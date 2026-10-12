"""Per-donor employer names and org donor names."""
import re
from collections import defaultdict

import pandas as pd

from fec.cleaning.employer_synonyms.canonical import canonical_key
from fec.config.constants import EMPLOYER_STATUS_VALUES

# legal suffixes and connectors carry no identity
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


# letter/digit runs for tokenizing employer names
_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")


# significant name tokens with legal suffixes removed
def _emp_core_tokens(name: str) -> frozenset:
    toks = _EMP_TOKEN_RE.findall(name.upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


# map short name to sole fuller name
def _employer_variant_map(names: list[str]) -> dict[str, str]:
    cores = {name: _emp_core_tokens(name) for name in names}
    by_core = defaultdict(list)
    for name in names:
        by_core[cores[name]].append(name)
    # longest spelling names each word set
    display = {core: max(members, key=len) for core, members in by_core.items()}

    remap = {}
    for core, members in by_core.items():
        fuller = [other for other in by_core if other > core and len(core & other) >= 2]
        widest = [other for other in fuller if not any(other < more for more in fuller)]
        target = display[widest[0]] if len(widest) == 1 else display[core]
        remap.update({name: target for name in members if name != target})
    return remap


# rewrite row employers using the variant map
def _apply_employer_variants(df: pd.DataFrame, indexes, remap: dict) -> int:
    changed = 0
    for index in indexes:
        current = df.at[index, "contributor_employer"]
        if isinstance(current, str) and current.strip() in remap:
            df.at[index, "contributor_employer"] = remap[current.strip()]
            changed += 1
    return changed


# unify employer variants within each donor
def canonicalize_donor_employers(df: pd.DataFrame) -> int:
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


# rename org donors to donor-side company spelling
def align_org_donor_company_names(df: pd.DataFrame) -> int:
    # most common donor-side spelling per key
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

    # name plus its comma-swapped form
    def _forms(n: str):
        out = [n]
        if "," in n:  # "CAPITAL, WHITE" to "WHITE CAPITAL"
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


# trailing legal form like CORP or LLC
_ORG_LEGAL_TAIL_RE = re.compile(
    r"(?:[\s,]+(?:CORPORATION|CORP|INCORPORATED|INC|COMPANY|CO|LLC|LLP|LTD|LP|PLLC)\.?)+$"
)


# drop legal suffix if bare name filed
def unify_org_donor_suffix_variants(df: pd.DataFrame) -> int:
    org = df["entity_type"] == "ORGANIZATION"
    if not org.any():
        return 0
    names = df.loc[org, "contributor_name"].dropna().astype(str).str.strip()
    distinct = set(names[names != ""])
    remap = {}
    for name in distinct:
        bare = _ORG_LEGAL_TAIL_RE.sub("", name).strip()
        # bare name must be filed, not guessed
        if bare and bare != name and bare in distinct:
            remap[name] = bare
    if not remap:
        return 0
    current = df["contributor_name"].where(df["contributor_name"].notna(), "").astype(str).str.strip()
    mask = org & current.isin(remap)
    df.loc[mask, "contributor_name"] = current[mask].map(remap)
    return int(mask.sum())
