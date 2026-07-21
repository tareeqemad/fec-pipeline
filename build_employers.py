#!/usr/bin/env python3
"""
Normalize employer HQ addresses into a separate employers.csv
=============================================================
The employer's corporate HQ is a property of the *employer*, not of every
contribution. Carrying employer_address on each row duplicated one company's
HQ ~12x. This extracts a clean one-row-per-employer dimension and slims the
contributions file.

    employers.csv              # one row per real company (active employees' firms)
      employer_name, employer_address/city/state/zip,
      employer_latitude/longitude, address_source (ai|manual), address_confidence

    contributions_cleaned.csv  # drops employer_address/city/state/zip/lat/lng;
                               # keeps contributor_employer (join key) + employer_status

Work location per individual:
    employer_status == 'active'        -> join contributor_employer -> employers.csv
    employer_status == 'self_employed' -> use the donor's own coordinates
    retired / not_employed             -> none

Variant collapse:
    contributor_employer (active) and previous_employer (retired) sometimes spell
    the same firm differently — "GOLDMAN SACHS" vs "GOLDMAN SACHS & CO" vs
    "AMAZON" vs "AMAZON.COM". Left alone they become separate employers.csv rows.
    We group every employer name by the pipeline's canonical_key and rewrite each
    variant to the spelling donors used most, so one company = one row.
"""
import json
from collections import Counter, defaultdict

import pandas as pd

from fec.env import CLEANED_CSV, DATA_DIR
from fec.log import get_logger, setup_logging
from fec.cleaning.employer_synonyms import canonical_key, restyle_legal_suffix
from fec.resolve.pipeline.helpers import _is_real_employer

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
    cache = json.load(open(RESOLVE_CACHE, encoding="utf-8"))
    out = {}
    for name, v in cache.items():
        m = (v.get("method") or "")
        source = "manual" if m == "manual_override" else ("ai" if m.startswith("ai") else m)
        out[name] = (source, v.get("confidence") or "")
    return out


def _canonical_employer_map(df: pd.DataFrame) -> dict:
    """variant employer name -> canonical name (most-used spelling in its
    canonical_key group). Only entries that actually change are returned."""
    freq: Counter = Counter()
    for col in EMP_NAME_COLS:
        if col in df.columns:
            freq.update(x for x in df[col].fillna("").str.strip() if x)

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
        canon = sorted(variants, key=lambda n: (-freq[n], len(n)))[0]
        for v in variants:
            if v != canon:
                mapping[v] = canon
    return mapping


def _apply_name_map(df: pd.DataFrame, mapping: dict) -> int:
    """Rewrite every employer-name column through `mapping`. Returns rows changed."""
    changed = 0
    for col in EMP_NAME_COLS:
        if col in df.columns and mapping:
            s = df[col].fillna("").str.strip()
            hit = s.isin(mapping)
            changed += int(hit.sum())
            df.loc[hit, col] = s[hit].map(mapping)
    return changed


def _dedupe_dimension(emp: pd.DataFrame) -> pd.DataFrame:
    """Real companies only (no RETIRED / NOT EMPLOYED / CAMPAIGN-COMMITTEE / …
    placeholders that can leak in via previous_employer), one row per
    employer_name; keep the row that has an HQ address."""
    emp = emp[emp["employer_name"].map(_is_real_employer)]
    return (emp.sort_values("employer_address", na_position="last")
               .drop_duplicates("employer_name", keep="first")
               .sort_values("employer_name").reset_index(drop=True))


def _restyle_names(df: pd.DataFrame) -> int:
    """Normalize legal-suffix STYLE on every employer-name column. This is the
    last writer of both files, so styling here is what actually survives —
    earlier passes get overwritten by the most-used-spelling collapse below."""
    changed = 0
    for col in EMP_NAME_COLS:
        if col not in df.columns:
            continue
        s = df[col].fillna("").str.strip()
        styled = s.map(lambda n: restyle_legal_suffix(n) if n else n)
        hit = styled != s
        changed += int(hit.sum())
        df.loc[hit, col] = styled[hit]
    return changed


def build() -> tuple[int, int]:
    df = pd.read_csv(CLEANED_CSV, dtype=str, keep_default_na=False, na_values=[""])

    # 0. Suffix style (", INC" -> " INC", "P.C" -> "PC") before grouping, so
    #    the canonical spelling picked below is already styled.
    n_styled = _restyle_names(df)
    if n_styled:
        logger.info(f"  restyled legal suffixes on {n_styled:,} contribution rows")

    # 1. Collapse spelling variants to one canonical name per company.
    mapping = _canonical_employer_map(df)
    rows = _apply_name_map(df, mapping)
    if mapping:
        logger.info(f"  collapsed {len(mapping):,} employer-name variants "
                    f"({rows:,} contribution rows remapped)")

    slimmed = "employer_address" not in df.columns

    if slimmed:
        # Address columns already removed — the HQ dimension lives in employers.csv.
        # Apply the same canonical map to it and re-dedupe so merged variants drop.
        n_emp = 0
        if EMPLOYERS_CSV.exists():
            emp = pd.read_csv(EMPLOYERS_CSV, dtype=str, keep_default_na=False, na_values=[""])
            emp["employer_name"] = emp["employer_name"].map(
                lambda n: mapping.get(restyle_legal_suffix(n), restyle_legal_suffix(n)))
            emp = _dedupe_dimension(emp)
            emp.to_csv(EMPLOYERS_CSV, index=False, na_rep="")
            n_emp = len(emp)
            logger.info(f"  ✓ employers.csv: {n_emp:,} companies (re-deduped by canonical name)")
        df.to_csv(CLEANED_CSV, index=False, na_rep="")
        return n_emp, len(df.columns)

    # 2. Fresh build: pull every referenced company HQ from the (now-canonical) rows.
    src = _source_map()
    status = df.get("employer_status", pd.Series("", index=df.index))
    active = (df[(df["entity_type"] == "INDIVIDUAL") & (status == "active")
                 & df["contributor_employer"].notna()][["contributor_employer"] + EMP_ADDR_COLS]
              .rename(columns={"contributor_employer": "employer_name"}))
    prev = (df[status.isin(["retired", "not_employed"])
               & df.get("previous_employer", pd.Series(pd.NA, index=df.index)).notna()]
            [["previous_employer"] + EMP_ADDR_COLS]
            .rename(columns={"previous_employer": "employer_name"}))

    emp = _dedupe_dimension(pd.concat([active, prev], ignore_index=True))
    emp["address_source"] = emp["employer_name"].map(lambda n: src.get(n, ("", ""))[0])
    emp["address_confidence"] = emp["employer_name"].map(lambda n: src.get(n, ("", ""))[1])
    no_addr = emp["employer_address"].isna()
    emp.loc[no_addr, ["address_source", "address_confidence"]] = ""
    emp.to_csv(EMPLOYERS_CSV, index=False, na_rep="")

    resolved = int(emp["employer_address"].notna().sum())
    logger.info(f"  ✓ employers.csv: {len(emp):,} companies ({resolved:,} with HQ address)")

    # Safety net: every company a contribution points at must exist as a row here.
    # Only REAL companies belong in the dimension — status values (RETIRED,
    # HOMEMAKER, NOT EMPLOYED, STUDENT, …) are deliberately absent, so filter the
    # referenced set through _is_real_employer or the warning fires on every
    # recognized status word rather than on a genuinely-missing company.
    names = set(emp["employer_name"].dropna())
    referenced = {n for n in
                  (set(active["employer_name"].dropna()) | set(prev["employer_name"].dropna()))
                  if _is_real_employer(n)}
    missing = referenced - names
    if missing:
        logger.warning(f"  ⚠ {len(missing)} referenced employer(s) missing from employers.csv: "
                       f"{sorted(missing)[:5]}")
    else:
        logger.info("  ✓ every referenced employer (current + previous) is present")

    # Slim the contributions file — drop the per-row employer address columns.
    slim = df.drop(columns=[c for c in EMP_ADDR_COLS if c in df.columns])
    slim.to_csv(CLEANED_CSV, index=False, na_rep="")
    logger.info(f"  ✓ contributions_cleaned.csv: {len(slim.columns)} columns "
                f"(dropped {len(EMP_ADDR_COLS)} employer_* address columns)")
    return len(emp), len(slim.columns)


def main():
    setup_logging(level="INFO")
    logger.info("── Normalizing employer addresses → employers.csv ──")
    build()


if __name__ == "__main__":
    main()
