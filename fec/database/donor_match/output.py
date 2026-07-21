"""Output functions: apply donor keys, export audit, show examples/stats."""

import csv
import hashlib
import math
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from fec.log import get_logger

from .constants import STATUS_EMPLOYERS
from .normalize import normalize_committee_name

logger = get_logger(__name__)


def individual_record_id(name, city, state) -> str:
    """The `name|city|state` record-id string used to key an individual donor."""
    return f"{name or ''}|{city or ''}|{state or ''}"


def individual_donor_key(name, city, state) -> str:
    """donor_key for an individual: sha256 of the record-id, first 12 hex chars.

    This is the canonical hash. It must stay identical everywhere a donor_key
    is generated (here and in leadership_matcher) so a person's leadership /
    key_accomplices row and their FEC contribution rows resolve to the same key.
    """
    rid = individual_record_id(name, city, state)
    return hashlib.sha256(rid.encode()).hexdigest()[:12]


def apply_donor_key(df: pd.DataFrame, rid_to_key: dict) -> pd.DataFrame:
    """Apply scored donor_key to DataFrame."""

    def _get_key(row):
        if row["entity_type"] != "INDIVIDUAL":
            norm = normalize_committee_name(row.get("contributor_name"))
            return hashlib.sha256(norm.encode()).hexdigest()[:12]
        rid = individual_record_id(
            row["contributor_name"], row["contributor_city"], row["contributor_state"]
        )
        return rid_to_key.get(rid, individual_donor_key(
            row["contributor_name"], row["contributor_city"], row["contributor_state"]
        ))

    df["donor_key"] = df.apply(_get_key, axis=1)
    return df


def merge_split_name_donors(df: pd.DataFrame) -> int:
    """Merge donors that are the same person split across donor_keys.

    The scorer blocks on (last|first_word), so a compound name FEC wrote two
    ways — "KAPNER, HILARY SMITH" (last=KAPNER) vs "SMITH KAPNER, HILARY"
    (last=SMITH KAPNER), or a first/last swap, or a hyphen vs space — lands
    in different blocks and never gets compared, leaving the person as two
    donor_keys.

    This post-pass catches them with a strict, safe rule:
      • the SORTED set of full-name word-tokens is identical
        (so "HILARY SMITH KAPNER" == "HILARY KAPNER SMITH"), AND
      • the 5-digit ZIP is identical (same physical home), AND
      • the occupation categories don't conflict (≤1 real category across
        the group — a retiree/blank/"OTHER" never blocks the merge).
    All rows in a matching group are repointed to one donor_key (the one
    with the most contributions — the dominant identity).

    Same name + same ZIP + compatible occupation = same person. Returns the
    number of contribution rows repointed.
    """
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    first = df["contributor_first_name"].fillna("").str.upper()
    last = df["contributor_last_name"].fillna("").str.upper()
    full = (first + " " + last).str.strip()
    # fingerprint: sorted alpha tokens of the full name (order-independent)
    fp = full.str.findall(r"[A-Z]+").map(lambda toks: " ".join(sorted(toks)))
    zip5 = df["contributor_zip"].fillna("")

    eligible = ind & (fp != "") & (zip5 != "")
    work = pd.DataFrame({
        "fp": fp[eligible], "z": zip5[eligible],
        "key": df.loc[eligible, "donor_key"],
        "occ": df.loc[eligible, "occupation_category"].fillna(""),
    })

    REAL_OCC = lambda s: s not in ("", "OTHER", "NOT EMPLOYED", "RETIRED")
    remap: dict[str, str] = {}
    for (fpv, z), grp in work.groupby(["fp", "z"]):
        keys = grp["key"].unique()
        if len(keys) < 2:
            continue
        # occupation conflict guard: >1 distinct REAL category → skip (leave
        # for manual review — could be two people sharing name + address).
        real_occs = {o for o in grp["occ"].unique() if REAL_OCC(o)}
        if len(real_occs) > 1:
            continue
        # winner = key with the most contribution rows (dominant identity)
        winner = grp["key"].value_counts().idxmax()
        for k in keys:
            if k != winner:
                remap[k] = winner

    if not remap:
        return 0
    mask = df["donor_key"].isin(remap)
    df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(remap)
    return int(mask.sum())


def apply_donor_dedup_merges(df: pd.DataFrame) -> int:
    """Apply curated, human-reviewed donor merges from
    data/database/donor_dedup_merges.csv.

    The matcher blocks on (surname | first WORD of first name), so a person who
    filed under both a nickname and the full name — "TIM WULIGER" vs "TIMOTHY
    WULIGER" — lands in two blocks and is never compared, leaving them as two
    donor_keys. build_donor_dedup_review surfaces such pairs; a human confirms
    the ones that are truly one person (shared exact street and/or employer at
    the same ZIP) into this CSV. Here we repoint every drop_donor_key row to its
    keep_donor_key (the fuller-name identity).

    Runs in the post-match phase, right after merge_split_name_donors.
    Deterministic + idempotent: donor_key is a stable hash of (name, city,
    state), so the same pairs resolve on every re-run; a key already absent is a
    harmless no-op. Chains (a drop that is another row's keep) are followed to a
    final key. Returns the number of contribution rows repointed.
    """
    path = (Path(__file__).resolve().parent.parent.parent.parent
            / "data" / "database" / "donor_dedup_merges.csv")
    if not path.exists() or "donor_key" not in df.columns:
        return 0
    remap: dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keep = (row.get("keep_donor_key") or "").strip()
            drop = (row.get("drop_donor_key") or "").strip()
            if keep and drop and keep != drop:
                remap[drop] = keep
    if not remap:
        return 0

    def _final(k):
        seen = set()
        while k in remap and k not in seen:
            seen.add(k)
            k = remap[k]
        return k

    mask = df["donor_key"].isin(remap)
    n = int(mask.sum())
    if n:
        df.loc[mask, "donor_key"] = df.loc[mask, "donor_key"].map(_final)
    return n


def canonicalize_donor_names(df: pd.DataFrame) -> int:
    """Give every row of one donor a single, clean first/last name.

    A person files under several spellings — "MATT" / "MATTHEW", or a
    compound name split two ways ("HILARY SMITH"+"KAPNER" vs
    "HILARY"+"SMITH KAPNER"). After donor_key groups them, this picks ONE
    canonical pair per donor and writes it to ALL their rows, so the cleaned
    CSV itself is consistent and the loader can store names verbatim — no
    name logic left downstream.

    Rules per donor_key (individuals only):
      • last_name  = the most common spelling the donor used.
      • first_name = the FULLEST spelling (longest), so a search for the
        nickname (a substring) or the full name both hit.
      • then drop any first-name word already in last_name, so a shared
        compound token ("SMITH") isn't stored twice and a surname copied
        into the first-name field ("COHEN"/"COHEN") collapses.

    The composite `contributor_name` is ALSO regenerated from the canonical
    pair (FEC "LAST, FIRST" form) so it can't drift from first/last — e.g. a
    donor whose first/last unified to FRANKLIN J. / HARBERG no longer keeps
    two stale composites ("HARBERG, FRANKLIN" + "HARBERG, FRANKLIN J").

    Returns the number of rows whose first, last, or composite name changed.
    """
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
        # Pick the fullest first name — but only among candidates that carry at
        # least one token that ISN'T the surname. A reversed/mis-parsed filing
        # ("GARY, HOFFMAN" → first="HOFFMAN") drops the surname into the first
        # field; without this guard that surname can be the LONGEST candidate,
        # get chosen, then stripped to nothing — wiping a real first name (GARY).
        fcands = [f for f in firsts if any(w.upper() not in last_words for w in f.split())]
        canon_first = max(fcands, key=len) if fcands else None
        if canon_first:
            kept = [w for w in canon_first.split() if w.upper() not in last_words]
            canon_first = " ".join(kept) or None

        # FEC composite, rebuilt from the canonical pair (matches the
        # v_contributions_cleaned CASE: "LAST, FIRST", or just LAST).
        canon_name = f"{canon_last}, {canon_first}" if canon_first else canon_last

        # Write canonical name fields to every row of this donor that differs.
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


# Legal-entity suffixes / connectors dropped when comparing two employer names
# for the "same firm" test — they carry no identity (LLP vs LLC vs INC is noise).
_EMP_DROP_TOKENS = frozenset({
    "LLP", "LLC", "INC", "PC", "CO", "CORP", "CORPORATION", "COMPANY", "LP",
    "LTD", "PLLC", "PA", "APC", "CHARTERED", "THE", "AND", "OF",
})


def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = re.findall(r"[A-Z0-9]+", (name or "").upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    """Within each donor, collapse employer-name variants that are the SAME firm
    written at different times — e.g. a law firm that added partners:
    "HARBERG HUVARD LLP" → "HARBERG HUVARD JACOBS WADLER LLP".

    Scoped PER DONOR, so it can never fuse two genuinely different firms that
    belong to different people (the risk that blocks a global merge). One person
    filing two names for their own employer is a strong same-firm signal.

    Same-firm test for a pair: one's significant-token set is a subset of the
    other's AND they share ≥2 significant tokens (so a lone shared surname like
    "SMITH" never merges "SMITH LLP" with "SMITH JONES LLP"). Each cluster
    collapses to its most COMPLETE form (most tokens, then longest). Unrelated
    employers with no shared core (e.g. INTERFAITH MINISTRIES) stay separate.

    Returns the number of contribution rows whose employer was rewritten.
    """
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

        # Union-find over this donor's employer names by the same-firm rule.
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
    """Give an ORGANIZATION donor the SAME name as the EMPLOYER entity of the
    same company, so a firm that DONATED and the firm where its people WORK are
    one identity — not two "companies". Reuses the EXISTING company-name unifier
    (canonical_key + the already-normalized contributor_employer values); adds NO
    new normalization. e.g. org-donor "KRAFT GROUP" -> "THE KRAFT GROUP" (the
    name its employees file), so a plain `donors.last_name = employers.name`
    match (see v_company) unifies the two — no view-time normalization needed.

    Returns the number of contribution rows whose org name was aligned.
    """
    if "entity_type" not in df.columns or "contributor_name" not in df.columns \
            or "contributor_employer" not in df.columns:
        return 0
    from fec.cleaning.employer_synonyms import canonical_key

    # Canonical employer display names (contributor_employer is already
    # EMPLOYER_SYNONYMS-normalized by clean()); keep the most-common spelling
    # per canonical_key — the same name the employers dimension will store.
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    emp = ind["contributor_employer"].dropna().astype(str).str.strip()
    emp = emp[(emp != "") & (~emp.str.upper().isin(STATUS_EMPLOYERS))]
    if emp.empty:
        return 0
    by_key: dict[str, str] = {}
    for name in emp.value_counts().index:            # value_counts → most common first
        k = canonical_key(name)
        if k and k not in by_key:
            by_key[k] = name

    def _forms(n: str):
        out = [n]
        if "," in n:                                  # "CAPITAL, WHITE" → "WHITE CAPITAL"
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


def _addr_fingerprint(street: str) -> str:
    """Order-independent address key: house number anchored, the rest sorted.

    "1742 GOLF RIDGE DR S" and "1742 S GOLF RIDGE DR" → same key, so a moved
    directional collapses. Anchoring the leading house number keeps grid
    addresses ("100 N 200 W" vs "200 N 100 W") distinct."""
    toks = re.findall(r"[A-Z0-9]+", (street or "").upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Within each donor, collapse street_1 values that are the SAME address
    written differently — same ZIP and same word-set with the house number
    anchored (a directional moved between prefix/suffix). Each such group
    collapses to its most common form (mode).

    Per-donor + same-ZIP + identical anchored word-set is a strong same-place
    signal, so this never merges two genuinely different residences (those
    differ in tokens or ZIP and stay separate — address history is preserved).

    Returns the number of contribution rows whose street_1 was rewritten.
    """
    if "contributor_street_1" not in df.columns:
        return 0
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    st_col = df.columns.get_loc("contributor_street_1")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        # bucket this donor's rows by (zip, anchored-fingerprint)
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s = df.iat[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            fp = _addr_fingerprint(s)
            if fp:
                buckets[(z, fp)].append(i)

        for (_z, _fp), rows in buckets.items():
            forms = [df.iat[i, st_col].strip() for i in rows]
            distinct = set(forms)
            if len(distinct) < 2:
                continue
            # canonical = most common spelling (tie → deterministic first)
            canon = max(sorted(distinct), key=lambda f: forms.count(f))
            for i in rows:
                if df.iat[i, st_col].strip() != canon:
                    df.iat[i, st_col] = canon
                    changed += 1
    return changed


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Within each donor, collapse street_2 unit designators that name the SAME
    unit written differently — "APT 1503" / "UNIT 1503" / "# 1503". Runs in the
    post-match phase (keyed on donor_key, AFTER canonicalize_donor_addresses has
    aligned street_1 / ZIP), so it also catches what the earlier name-scoped
    cleaning pass (_unify_unit_designators) missed — a donor whose two unit
    spellings carried a now-reconciled ZIP or name. Bucketed per donor by
    (street_1, ZIP, bare unit id); a bucket with >1 raw street_2 collapses to
    the donor's dominant form (most rows, then longest). Distinct units keep a
    different id and two people never share a donor_key, so neither merges.

    Returns the number of contribution rows whose street_2 was rewritten.
    """
    if "contributor_street_2" not in df.columns or "donor_key" not in df.columns:
        return 0
    ind = df["entity_type"] == "INDIVIDUAL" if "entity_type" in df.columns \
        else pd.Series(True, index=df.index)
    if not ind.any():
        return 0
    from fec.cleaning.pipeline.address_fixes import _unit_core

    st1_col = df.columns.get_loc("contributor_street_1") if "contributor_street_1" in df.columns else None
    st2_col = df.columns.get_loc("contributor_street_2")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s2 = df.iat[i, st2_col]
            if not (isinstance(s2, str) and s2.strip()):
                continue
            core = _unit_core(s2)
            if not core:
                continue
            s1 = df.iat[i, st1_col] if st1_col is not None else ""
            s1 = s1.strip() if isinstance(s1, str) else ""
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            buckets[(s1, z, core)].append(i)

        for _k, rows in buckets.items():
            forms = [df.iat[i, st2_col].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=lambda f: (forms.count(f), len(f)))
            for i in rows:
                if df.iat[i, st2_col].strip() != canon:
                    df.iat[i, st2_col] = canon
                    changed += 1
    return changed


def _pobox_num(street: str) -> str:
    """Extract the box number from a PO-box street, else ''."""
    m = re.search(r'\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)', str(street).upper())
    return m.group(1) if m else ''


def _is_insertion_typo(a: str, b: str) -> bool:
    """True iff one string is the other with EXACTLY one extra digit inserted
    (len differs by 1 and the shorter is a subsequence of the longer). This
    distinguishes a typo (190669 vs 1900669) from two genuinely different boxes
    of the same length (424 vs 524 — never merged)."""
    if abs(len(a) - len(b)) != 1:
        return False
    s, l = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    for ch in l:
        if i < len(s) and ch == s[i]:
            i += 1
    return i == len(s)


def canonicalize_donor_pobox_typos(df: pd.DataFrame) -> int:
    """Within each donor + ZIP, collapse PO-box numbers that differ by exactly
    one inserted/deleted digit — a data-entry typo for the SAME box (e.g. a
    donor filing PO BOX 190669 / 1900669 / 1906691 at ZIP 63119). The canonical
    form is the most-frequent box (ties → shortest). Conservative by design:
    same-length different numbers are never merged, so two real boxes stay
    distinct and address history is preserved. Returns rows rewritten.

    Applies to ALL entity types (committees file from PO boxes too).
    """
    if "contributor_street_1" not in df.columns or "donor_key" not in df.columns:
        return 0
    st_col = df.columns.get_loc("contributor_street_1")
    zip_col = df.columns.get_loc("contributor_zip") if "contributor_zip" in df.columns else None
    changed = 0

    for _, idx in df.groupby("donor_key").groups.items():
        by_zip: dict[str, list] = defaultdict(list)
        for i in idx:
            s = df.iat[i, st_col]
            bn = _pobox_num(s) if isinstance(s, str) else ""
            if not bn:
                continue
            z = df.iat[i, zip_col] if zip_col is not None else ""
            z = z if isinstance(z, str) else ""
            by_zip[z].append((i, s.strip(), bn))

        for _z, rows in by_zip.items():
            nums = sorted({bn for _, _, bn in rows})
            if len(nums) < 2:
                continue
            # union typo-related box numbers into clusters
            parent = {n: n for n in nums}
            def find(x):
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x
            for a in nums:
                for b in nums:
                    if a < b and _is_insertion_typo(a, b):
                        parent[find(a)] = find(b)
            clusters: dict[str, list] = defaultdict(list)
            for n in nums:
                clusters[find(n)].append(n)

            freq: dict[str, int] = defaultdict(int)
            for _, _, bn in rows:
                freq[bn] += 1
            for members in clusters.values():
                if len(members) < 2:
                    continue
                canon_box = max(members, key=lambda n: (freq[n], -len(n)))
                canon_forms = [f for _, f, bn in rows if bn == canon_box]
                canon_full = max(set(canon_forms), key=canon_forms.count)
                for i, f, bn in rows:
                    if bn in members and f != canon_full:
                        df.iat[i, st_col] = canon_full
                        changed += 1
    return changed


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def canonicalize_donor_addresses_geo(df: pd.DataFrame, radius_m: float = 50.0) -> int:
    """Within each donor, collapse street_1 strings that GEOCODE to the same
    physical spot (≤ radius_m apart) even when the text differs beyond what the
    string pass caught — e.g. "123 MAIN ST" vs "123 MAIN STREET STE 5".

    Runs AFTER geocoding (needs latitude/longitude); a no-op without them. Only
    precise geocodes are used — city/zip-centroid levels are skipped so coarse
    matches never fuse distinct addresses. Per donor + tight radius keeps two
    real residences apart. Collapses to the most common form (and aligns coords).

    Returns the number of rows whose street_1 was rewritten.
    """
    needed = {"latitude", "longitude", "donor_key", "contributor_street_1"}
    if not needed <= set(df.columns):
        return 0
    ind = df["entity_type"] == "INDIVIDUAL" if "entity_type" in df.columns \
        else pd.Series(True, index=df.index)
    if not ind.any():
        return 0

    st_col = df.columns.get_loc("contributor_street_1")
    lat_col = df.columns.get_loc("latitude")
    lng_col = df.columns.get_loc("longitude")
    level_col = df.columns.get_loc("geocode_level") if "geocode_level" in df.columns else None
    changed = 0

    for _, idx in df[ind].groupby("donor_key").groups.items():
        pts = []  # (row_i, street, lat, lng)
        for i in idx:
            s = df.iat[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            if level_col is not None:
                lvl = str(df.iat[i, level_col]).lower()
                if any(t in lvl for t in ("city", "zip", "centroid", "state")):
                    continue
            try:
                lat, lng = float(df.iat[i, lat_col]), float(df.iat[i, lng_col])
            except (TypeError, ValueError):
                continue
            if math.isnan(lat) or math.isnan(lng):
                continue
            pts.append((i, s.strip(), lat, lng))
        if len(pts) < 2:
            continue

        parent = list(range(len(pts)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for a in range(len(pts)):
            for b in range(a + 1, len(pts)):
                if _haversine_m(pts[a][2], pts[a][3], pts[b][2], pts[b][3]) <= radius_m:
                    parent[find(a)] = find(b)

        clusters = defaultdict(list)
        for k in range(len(pts)):
            clusters[find(k)].append(k)

        for members in clusters.values():
            forms = [pts[m][1] for m in members]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=forms.count)
            rep = next(m for m in members if pts[m][1] == canon)
            rlat, rlng = pts[rep][2], pts[rep][3]
            for m in members:
                i = pts[m][0]
                if df.iat[i, st_col].strip() != canon:
                    df.iat[i, st_col] = canon
                    df.iat[i, lat_col] = rlat
                    df.iat[i, lng_col] = rlng
                    changed += 1
    return changed


def build_donor_dedup_review(df: pd.DataFrame, out_dir) -> int:
    """Detection-only: flag likely-same-person donor pairs the matcher did NOT
    merge, for human review. Writes data/donor_dedup_review.csv — it NEVER
    merges anything (the matcher already auto-merges the confident cases; this
    surfaces the residue a human should judge).

    Two DISTINCT donor_keys are flagged when they share a ZIP and surname and
    their first names are related:
      • identical first name (the matcher usually split them on a different
        city — a strong "probably the same person who moved" signal),
      • a nickname pair (BILL/WILLIAM via NICKNAME_MAP), or
      • one is an initial / prefix of the other (J/JOHN, ROB/ROBERT).
    Known do-not-merge names are excluded. Pairs are sorted by combined
    contribution amount, so the biggest (most worth a look) surface first.

    Returns the number of candidate pairs written (0 if out_dir is None).
    """
    if not out_dir:
        return 0
    need = {"donor_key", "entity_type", "contributor_last_name",
            "contributor_first_name", "contributor_city",
            "contributor_state", "contributor_zip"}
    if not need <= set(df.columns):
        return 0
    from itertools import combinations
    from .constants import NICKNAME_MAP, _is_blocked_merge

    ind = df[df["entity_type"] == "INDIVIDUAL"]
    if ind.empty:
        return 0
    amt = pd.to_numeric(df.get("contribution_receipt_amount"), errors="coerce").fillna(0)
    work = df.assign(_amt=amt).loc[ind.index]

    def _first_str(s):
        s = s.dropna().astype(str)
        s = s[s.str.strip() != ""]
        return s.iloc[0] if len(s) else ""

    g = work.groupby("donor_key")
    summ = pd.DataFrame({
        "last":   g["contributor_last_name"].agg(_first_str).str.upper(),
        "first":  g["contributor_first_name"].agg(_first_str).str.upper(),
        "city":   g["contributor_city"].agg(_first_str),
        "state":  g["contributor_state"].agg(_first_str),
        "n":      g.size(),
        "amount": g["_amt"].sum(),
    })

    # (donor_key, ZIP) pairs, blocked by (ZIP, surname). A donor with two ZIPs
    # appears in two blocks, so a move doesn't hide a duplicate.
    zp = ind[["donor_key", "contributor_zip"]].dropna()
    zp = zp[zp["contributor_zip"].astype(str).str.strip() != ""].drop_duplicates()
    zp = zp.join(summ["last"], on="donor_key")

    def _root(first):
        f0 = first.split()[0] if first else ""
        return NICKNAME_MAP.get(f0, f0)

    def _relation(fa, fb):
        a0 = fa.split()[0] if fa else ""
        b0 = fb.split()[0] if fb else ""
        if not a0 or not b0:
            return ""
        if a0 == b0:
            return "same first name (different city?)"
        if _root(fa) == _root(fb):
            return f"nickname {a0}/{b0}"
        s, l = sorted((a0, b0), key=len)
        if len(s) >= 1 and l.startswith(s) and s != l:
            return f"initial/prefix {s}->{l}"
        return ""

    seen, out = set(), []
    for (z, last), grp in zp.groupby(["contributor_zip", "last"]):
        if not last:
            continue
        keys = sorted(grp["donor_key"].unique())
        if len(keys) < 2:
            continue
        for ka, kb in combinations(keys, 2):
            if (ka, kb) in seen:
                continue
            seen.add((ka, kb))
            fa, fb = summ.at[ka, "first"], summ.at[kb, "first"]
            reason = _relation(fa, fb)
            if not reason:
                continue
            if _is_blocked_merge(f"{fa} {last}", f"{fb} {last}"):
                continue
            out.append({
                "reason": reason, "zip": z, "last_name": last,
                "donor_key_a": ka, "first_a": fa,
                "city_a": summ.at[ka, "city"], "state_a": summ.at[ka, "state"],
                "donations_a": int(summ.at[ka, "n"]), "amount_a": round(float(summ.at[ka, "amount"]), 2),
                "donor_key_b": kb, "first_b": fb,
                "city_b": summ.at[kb, "city"], "state_b": summ.at[kb, "state"],
                "donations_b": int(summ.at[kb, "n"]), "amount_b": round(float(summ.at[kb, "amount"]), 2),
                "combined_amount": round(float(summ.at[ka, "amount"] + summ.at[kb, "amount"]), 2),
            })
    if not out:
        return 0
    out.sort(key=lambda r: -r["combined_amount"])
    path = Path(out_dir) / "donor_dedup_review.csv"
    pd.DataFrame(out).to_csv(path, index=False, na_rep="")
    logger.info(f"  ✓ Donor-dedup review → {path} ({len(out):,} candidate pairs)")
    return len(out)


def export_audit(audit_log: list, path: Path) -> None:
    """Export merge audit log as CSV for manual review."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "norm_name", "rid_a", "rid_b", "score", "merged", "signals"
        ])
        writer.writeheader()
        for row in sorted(audit_log, key=lambda x: -x["score"]):
            writer.writerow(row)
    logger.info(f"  \u2713 Audit \u2192 {path} ({len(audit_log):,} pairs)")


def show_examples(df: pd.DataFrame, rid_to_key: dict, n: int) -> None:
    """Show merge examples."""
    key_to_rids = defaultdict(set)
    for rid, key in rid_to_key.items():
        key_to_rids[key].add(rid)

    merges = [(k, rids) for k, rids in key_to_rids.items() if len(rids) > 1]
    merges.sort(key=lambda x: -len(x[1]))

    logger.info(f"\n  \u2500\u2500 Top {min(n, len(merges))} Merges \u2500\u2500")
    for key, rids in merges[:n]:
        parts = [r.split("|") for r in sorted(rids)]
        name = parts[0][0]
        logger.info(f"  [{key}] {name}")
        for p in parts:
            loc = f"{p[1]}, {p[2]}" if len(p) >= 3 else p[0]
            logger.info(f"      \u2192 {loc}")


def show_stats(df: pd.DataFrame, rid_to_key: dict) -> None:
    """Show detailed statistics."""
    indiv = df[df["entity_type"] == "INDIVIDUAL"].copy()
    logger.info(f"\n  \u2500\u2500 Detailed Stats \u2500\u2500")

    indiv["record_id"] = (
        indiv["contributor_name"].fillna("") + "|" +
        indiv["contributor_city"].fillna("") + "|" +
        indiv["contributor_state"].fillna("")
    )
    indiv["donor_key"] = indiv["record_id"].map(rid_to_key)

    donor_counts = indiv.groupby("donor_key").size()
    logger.info(f"  Donations per donor:")
    logger.info(f"    Mean:   {donor_counts.mean():.1f}")
    logger.info(f"    Median: {donor_counts.median():.0f}")
    logger.info(f"    Max:    {donor_counts.max()}")

    key_to_rids = defaultdict(set)
    for rid, key in rid_to_key.items():
        key_to_rids[key].add(rid)

    merge_sizes = [len(rids) for rids in key_to_rids.values() if len(rids) > 1]
    logger.info(f"\n  Merge statistics:")
    logger.info(f"    Donors with merges: {len(merge_sizes):,}")
    logger.info(f"    2-way merges:       {sum(1 for s in merge_sizes if s==2):,}")
    logger.info(f"    3-way merges:       {sum(1 for s in merge_sizes if s==3):,}")
    logger.info(f"    4+ way merges:      {sum(1 for s in merge_sizes if s>=4):,}")
