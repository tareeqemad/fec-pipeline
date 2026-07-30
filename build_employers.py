#!/usr/bin/env python3
"""Extract the one-row-per-employer HQ dimension (employers.csv) and slim the contributions file."""
import json
from collections import Counter, defaultdict

import pandas as pd

from fec.env import CLEANED_CSV, DATA_DIR
from fec.log import get_logger, setup_logging
from fec.cleaning.employer_synonyms import canonical_key, restyle_legal_suffix
from fec.cleaning.previous_employer import is_real_employer

logger = get_logger(__name__)

EMPLOYERS_CSV = DATA_DIR / "employers.csv"
BRANCHES_CSV = DATA_DIR / "employer_branches.csv"
RESOLVE_CACHE = DATA_DIR / "resolve_employer_addr.json"
BRANCH_CACHE = DATA_DIR / "resolve_employer_branch.json"

EMP_ADDR_COLS = [
    "employer_address", "employer_city", "employer_state", "employer_zip",
    "employer_latitude", "employer_longitude",
]
EMP_NAME_COLS = ["contributor_employer", "previous_employer"]


def _source_map() -> dict:
    """employer name -> (address_source, address_confidence) from the resolve cache."""
    if not RESOLVE_CACHE.exists():
        return {}
    with open(RESOLVE_CACHE, encoding="utf-8") as handle:
        cache = json.load(handle)
    out = {}
    for name, entry in cache.items():
        method = (entry.get("method") or "")
        source = "manual" if method == "manual_override" else ("ai" if method.startswith("ai") else method)
        out[name] = (source, entry.get("confidence") or "")
    return out


def _branch_states() -> dict:
    """employer name (upper) -> set of donor states that resolve to a branch office rather than the HQ."""
    if not BRANCH_CACHE.exists():
        return {}
    with open(BRANCH_CACHE, encoding="utf-8") as handle:
        cache = json.load(handle)
    states: dict = defaultdict(set)
    for key, entry in cache.items():
        if "|" not in key or not (entry or {}).get("employer_address"):
            continue
        name, donor_state = key.rsplit("|", 1)
        states[name].add(donor_state)
    return states


def _write_branches(df: pd.DataFrame, branch_states: dict) -> int:
    """Write one row per (employer, donor state) branch office, with the coordinates geocode already resolved. Returns rows written."""
    if not branch_states:
        if BRANCHES_CSV.exists():
            BRANCHES_CSV.unlink()
        return 0

    is_branch = df.apply(
        lambda row: str(row.get("contributor_state") or "").upper()
        in branch_states.get(str(row.get("contributor_employer") or "").upper(), set()),
        axis=1,
    )
    branches = (df[is_branch][["contributor_employer", "contributor_state"] + EMP_ADDR_COLS]
                .rename(columns={"contributor_employer": "employer_name",
                                 "contributor_state": "donor_state"})
                .dropna(subset=["employer_address"])
                .drop_duplicates(subset=["employer_name", "donor_state"])
                .sort_values(["employer_name", "donor_state"])
                .reset_index(drop=True))
    branches.to_csv(BRANCHES_CSV, index=False, na_rep="")
    return len(branches)


def _canonical_employer_map(df: pd.DataFrame) -> dict:
    """variant employer name -> most-used spelling in its canonical_key group; only entries that actually change are returned."""
    freq: Counter = Counter()
    for col in EMP_NAME_COLS:
        freq.update(name for name in df[col].fillna("").str.strip() if name)

    groups: dict = defaultdict(list)
    for name in freq:
        key = canonical_key(name)
        if key:
            groups[key].append(name)

    mapping: dict = {}
    for variants in groups.values():
        if len(variants) < 2:
            continue
        # canonical = most contributions, then the shorter (usually cleaner)
        # spelling. The final key is the name itself: without it two equally
        # common, equally long spellings are separated only by the order they
        # happened to appear in, so the winner flips between runs.
        canonical = sorted(variants, key=lambda name: (-freq[name], len(name), name))[0]
        for variant in variants:
            if variant != canonical:
                mapping[variant] = canonical
    return mapping


def _apply_name_map(df: pd.DataFrame, mapping: dict) -> int:
    """Rewrite every employer-name column through `mapping`. Returns rows changed."""
    if not mapping:
        return 0
    changed = 0
    for col in EMP_NAME_COLS:
        names = df[col].fillna("").str.strip()
        hit = names.isin(mapping)
        changed += int(hit.sum())
        df.loc[hit, col] = names[hit].map(mapping)
    return changed


def _hq_addresses() -> dict:
    """employer name (upper) -> the HQ street the resolve cache holds for it."""
    if not RESOLVE_CACHE.exists():
        return {}
    with open(RESOLVE_CACHE, encoding="utf-8") as handle:
        cache = json.load(handle)
    return {name: (entry.get("employer_address") or "")
            for name, entry in cache.items() if entry.get("employer_address")}


def _dedupe_dimension(emp: pd.DataFrame) -> pd.DataFrame:
    """Real companies only (drop RETIRED/NOT EMPLOYED/... placeholders), one row per employer_name.

    A company can reach this frame with two different addresses - once as
    somebody's current employer and once as somebody's previous employer, whose
    lookups go through different cache keys. Rank the candidates instead of
    taking whichever address sorts first, or the pick flips between runs and
    the md5 gate stops meaning anything.
    """
    emp = emp[emp["employer_name"].map(is_real_employer)]
    hq = _hq_addresses()

    def rank(row) -> tuple:
        address = row["employer_address"]
        if not isinstance(address, str) or not address:
            return (2, "")
        # the cache entry for this exact name is the company's real HQ
        is_hq = address != hq.get(str(row["employer_name"]).upper())
        return (int(is_hq), address)

    ordered = emp.assign(_rank=[rank(row) for _, row in emp.iterrows()])
    return (ordered.sort_values(["employer_name", "_rank"], kind="stable")
                   .drop_duplicates("employer_name", keep="first")
                   .drop(columns="_rank")
                   .sort_values("employer_name").reset_index(drop=True))


def _restyle_names(df: pd.DataFrame) -> int:
    """Normalize legal-suffix style on every employer-name column; this file is the last writer of both files, so styling here is what survives."""
    changed = 0
    for col in EMP_NAME_COLS:
        names = df[col].fillna("").str.strip()
        styled = names.map(lambda name: restyle_legal_suffix(name) if name else name)
        hit = styled != names
        changed += int(hit.sum())
        df.loc[hit, col] = styled[hit]
    return changed


def build() -> tuple[int, int]:
    df = pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, na_values=[""])

    # 0. suffix style before grouping, so the canonical spelling picked below is already styled
    n_styled = _restyle_names(df)
    if n_styled:
        logger.info(f"  restyled legal suffixes on {n_styled:,} contribution rows")

    # 1. collapse spelling variants to one canonical name per company
    mapping = _canonical_employer_map(df)
    rows = _apply_name_map(df, mapping)
    if mapping:
        logger.info(f"  collapsed {len(mapping):,} employer-name variants "
                    f"({rows:,} contribution rows remapped)")

    slimmed = "employer_address" not in df.columns

    if slimmed:
        # address columns already gone; apply the same map to employers.csv and re-dedupe so merged variants drop
        n_emp = 0
        if EMPLOYERS_CSV.exists():
            emp = pd.read_csv(EMPLOYERS_CSV, dtype=str, keep_default_na=False, na_values=[""])
            styled = emp["employer_name"].map(restyle_legal_suffix)
            emp["employer_name"] = styled.map(lambda name: mapping.get(name, name))
            emp = _dedupe_dimension(emp)
            emp.to_csv(EMPLOYERS_CSV, index=False, na_rep="")
            n_emp = len(emp)
            logger.info(f"  employers.csv: {n_emp:,} companies (re-deduped by canonical name)")
        df.to_csv(CLEANED_CSV, index=False, na_rep="")
        return n_emp, len(df.columns)

    # 2. fresh build: pull every referenced company HQ from the now-canonical rows
    src = _source_map()
    branch_states = _branch_states()
    n_branches = _write_branches(df, branch_states)
    if n_branches:
        logger.info(f"  employer_branches.csv: {n_branches:,} branch office(s) "
                    f"for donors outside the HQ state")

    status = df["employer_status"]
    # A branch row carries the donor's local office, not the company HQ. Blank
    # its address (rather than dropping the row) so the HQ is picked from a
    # non-branch row while the company still gets a dimension entry even when
    # every one of its donors sits at a branch.
    hq_view = df
    if branch_states:
        on_branch = df.apply(
            lambda row: str(row.get("contributor_state") or "").upper()
            in branch_states.get(str(row.get("contributor_employer") or "").upper(), set()),
            axis=1,
        )
        hq_view = df.copy()
        hq_view.loc[on_branch, EMP_ADDR_COLS] = None

    active = (hq_view[(hq_view["entity_type"] == "INDIVIDUAL") & (status == "active")
                      & hq_view["contributor_employer"].notna()]
              [["contributor_employer"] + EMP_ADDR_COLS]
              .rename(columns={"contributor_employer": "employer_name"}))
    prev = (hq_view[status.isin(["retired", "not_employed"])
                    & hq_view["previous_employer"].notna()]
            [["previous_employer"] + EMP_ADDR_COLS]
            .rename(columns={"previous_employer": "employer_name"}))

    emp = _dedupe_dimension(pd.concat([active, prev], ignore_index=True))
    emp["address_source"] = emp["employer_name"].map(lambda n: src.get(n, ("", ""))[0])
    emp["address_confidence"] = emp["employer_name"].map(lambda n: src.get(n, ("", ""))[1])
    no_addr = emp["employer_address"].isna()
    emp.loc[no_addr, ["address_source", "address_confidence"]] = ""
    emp.to_csv(EMPLOYERS_CSV, index=False, na_rep="")

    resolved = int(emp["employer_address"].notna().sum())
    logger.info(f"  employers.csv: {len(emp):,} companies ({resolved:,} with HQ address)")

    # safety net: every referenced company must exist as a row here. Status values
    # (RETIRED, STUDENT, ...) are deliberately absent, so filter the referenced set
    # through is_real_employer or this warns on every recognized status word.
    names = set(emp["employer_name"].dropna())
    referenced = {n for n in
                  (set(active["employer_name"].dropna()) | set(prev["employer_name"].dropna()))
                  if is_real_employer(n)}
    missing = referenced - names
    if missing:
        logger.warning(f"  {len(missing)} referenced employer(s) missing from employers.csv: "
                       f"{sorted(missing)[:5]}")
    else:
        logger.info("  every referenced employer (current + previous) is present")

    # drop the per-row employer address columns from the contributions file
    slim = df.drop(columns=[c for c in EMP_ADDR_COLS if c in df.columns])
    slim.to_csv(CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  contributions_cleaned.csv: {len(slim.columns)} columns "
                f"(dropped {len(EMP_ADDR_COLS)} employer_* address columns)")
    return len(emp), len(slim.columns)


def main():
    setup_logging(level="INFO")
    logger.info("-- Normalizing employer addresses -> employers.csv --")
    build()


if __name__ == "__main__":
    main()
