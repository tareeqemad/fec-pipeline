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
RESOLVE_CACHE = DATA_DIR / "resolve_employer_addr.json"

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
        # canonical = most contributions, then the shorter (usually cleaner) spelling
        canonical = sorted(variants, key=lambda name: (-freq[name], len(name)))[0]
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


def _dedupe_dimension(emp: pd.DataFrame) -> pd.DataFrame:
    """Real companies only (drop RETIRED/NOT EMPLOYED/... placeholders), one row per employer_name, preferring the row with an HQ address."""
    emp = emp[emp["employer_name"].map(is_real_employer)]
    return (emp.sort_values("employer_address", na_position="last")
               .drop_duplicates("employer_name", keep="first")
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
    status = df["employer_status"]
    active = (df[(df["entity_type"] == "INDIVIDUAL") & (status == "active")
                 & df["contributor_employer"].notna()][["contributor_employer"] + EMP_ADDR_COLS]
              .rename(columns={"contributor_employer": "employer_name"}))
    prev = (df[status.isin(["retired", "not_employed"])
               & df["previous_employer"].notna()]
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
