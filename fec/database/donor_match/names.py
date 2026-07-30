"""Per-donor canonicalization of person, employer, and org-donor names."""

import re
from collections import defaultdict

import pandas as pd

from .constants import STATUS_EMPLOYERS

# legal suffixes/connectors carry no identity when comparing employer names
_EMP_DROP_TOKENS = frozenset({
    "LLP", "LLC", "INC", "PC", "CO", "CORP", "CORPORATION", "COMPANY", "LP",
    "LTD", "PLLC", "PA", "APC", "CHARTERED", "THE", "AND", "OF",
})
_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")


def canonicalize_donor_names(df: pd.DataFrame) -> int:
    """Write one canonical first/last (most common last, fullest first, shared surname tokens dropped) plus a rebuilt LAST, FIRST composite to every row of each donor; returns rows changed."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    changed = 0
    fn_col = df.columns.get_loc("contributor_first_name")
    ln_col = df.columns.get_loc("contributor_last_name")
    cn_col = df.columns.get_loc("contributor_name") if "contributor_name" in df.columns else None

    for _, idx in df[ind].groupby("donor_key").groups.items():
        rows = df.loc[idx]
        lasts = rows["contributor_last_name"].dropna().map(str).str.strip()
        lasts = lasts[lasts != ""]
        firsts = rows["contributor_first_name"].dropna().map(str).str.strip()
        firsts = firsts[firsts != ""]
        if lasts.empty:
            continue
        canon_last = lasts.mode().iloc[0]
        last_words = set(canon_last.upper().split())
        # candidates must carry a non-surname token: a reversed filing's
        # surname-as-first could otherwise win, then strip to nothing
        fcands = [f for f in firsts if any(w.upper() not in last_words for w in f.split())]
        canon_first = max(fcands, key=len) if fcands else None
        if canon_first:
            kept = [w for w in canon_first.split() if w.upper() not in last_words]
            canon_first = " ".join(kept) or None

        # composite rebuilt from the canonical pair (matches the v_contributions_cleaned CASE)
        canon_name = f"{canon_last}, {canon_first}" if canon_first else canon_last

        for i in idx:
            cur_f = df.iat[i, fn_col]
            cur_l = df.iat[i, ln_col]
            cf = cur_f if (isinstance(cur_f, str) and cur_f.strip()) else None
            cl = cur_l if (isinstance(cur_l, str) and cur_l.strip()) else None
            cur_n = df.iat[i, cn_col] if cn_col is not None else canon_name
            if cf != canon_first or cl != canon_last or cur_n != canon_name:
                df.iat[i, fn_col] = canon_first
                df.iat[i, ln_col] = canon_last
                if cn_col is not None:
                    df.iat[i, cn_col] = canon_name
                changed += 1
    return changed


def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = _EMP_TOKEN_RE.findall((name or "").upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    """Collapse per-donor employer variants of the same firm (token subset + >=2 shared tokens) to the most complete form; returns rows rewritten."""
    if "contributor_employer" not in df.columns:
        return 0
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    emp_col = df.columns.get_loc("contributor_employer")
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        sub = df.loc[idx, "contributor_employer"].dropna().map(str).str.strip()
        names = [n for n in sub.unique() if n and n.upper() not in STATUS_EMPLOYERS]
        if len(names) < 2:
            continue
        cores = {n: _emp_core_tokens(n) for n in names}

        # union-find over this donor's employer names by the same-firm rule
        parent = {n: n for n in names}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a_i in range(len(names)):
            for b_i in range(a_i + 1, len(names)):
                a, b = names[a_i], names[b_i]
                ca, cb = cores[a], cores[b]
                if len(ca & cb) >= 2 and (ca <= cb or cb <= ca):
                    parent[find(a)] = find(b)

        clusters: dict[str, list] = defaultdict(list)
        for n in names:
            clusters[find(n)].append(n)

        remap = {}
        for members in clusters.values():
            if len(members) < 2:
                continue
            canon = max(members, key=lambda n: (len(cores[n]), len(n)))
            for n in members:
                if n != canon:
                    remap[n] = canon
        if not remap:
            continue

        for i in idx:
            cur = df.iat[i, emp_col]
            if isinstance(cur, str) and cur.strip() in remap:
                df.iat[i, emp_col] = remap[cur.strip()]
                changed += 1
    return changed


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """Rename ORGANIZATION donors to the canonical employer spelling of the same company (reuses canonical_key, adds no new normalization); returns rows aligned."""
    if "entity_type" not in df.columns or "contributor_name" not in df.columns \
            or "contributor_employer" not in df.columns:
        return 0
    from fec.cleaning.employer_synonyms import canonical_key

    # canonical display name per canonical_key = the donor-side spelling seen most
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    emp = ind["contributor_employer"].dropna().astype(str).str.strip()
    emp = emp[(emp != "") & (~emp.str.upper().isin(STATUS_EMPLOYERS))]
    if emp.empty:
        return 0
    by_key: dict[str, str] = {}
    for name in emp.value_counts().index:            # value_counts: most common first
        k = canonical_key(name)
        if k and k not in by_key:
            by_key[k] = name

    def _forms(n: str):
        out = [n]
        if "," in n:                                  # "CAPITAL, WHITE" -> "WHITE CAPITAL"
            a, b = n.split(",", 1)
            out.append(f"{b.strip()} {a.strip()}")
        return out

    org_names = df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"].dropna().unique()
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
