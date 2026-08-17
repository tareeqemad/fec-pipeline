"""Restore work fields for committee rows reclassified as individuals."""

from __future__ import annotations

import numpy as np
import pandas as pd

from fec.cleaning.occupations import _categorize, _normalize_text, map_occupation_fixes
from fec.config.constants import OK_SHORT_EMPLOYERS, OK_SHORT_OCCUPATIONS
from fec.config.data import MISSING_VALUES
from fec.config.employers import EMPLOYER_NORMALIZE
from fec.config.occupation_rules.rules import OCCUPATION_NORMALIZE, SWAP_JOB_TITLES

from .reclassify import _ORG_BUSINESS_RE


def _restore_individual_occupations(
    df: pd.DataFrame,
    raw_occupations: pd.Series,
    raw_employers: pd.Series,
    log,
) -> None:
    has_occupation = raw_occupations.notna() & raw_occupations.astype(
        str
    ).str.strip().ne("")
    if not has_occupation.any():
        return

    restored, _ = _normalize_text(
        raw_occupations[has_occupation],
        OCCUPATION_NORMALIZE,
        collapse_retire=True,
    )
    raw_employer, _ = _normalize_text(
        raw_employers[has_occupation],
        EMPLOYER_NORMALIZE,
    )
    swapped = raw_employer.isin(SWAP_JOB_TITLES) & (
        restored.str.contains(_ORG_BUSINESS_RE, na=False) | restored.eq("SELF-EMPLOYED")
    )
    restored.loc[swapped] = raw_employer.loc[swapped]
    restored = restored[~restored.isin(MISSING_VALUES) & restored.notna()]
    if restored.empty:
        return

    df.loc[restored.index, "contributor_occupation"] = restored
    df.loc[restored.index, "occupation_category"] = _categorize(restored)
    df.loc[restored.index, "occupation_status"] = "DISCLOSED"
    map_occupation_fixes(df, restored.index)

    log(
        f"  -> restored {len(restored):,} occupations for reclassified "
        "committee->individual records"
    )


def _restore_individual_employers(
    df: pd.DataFrame,
    reclassified: pd.Series,
    raw_employers: pd.Series,
) -> None:
    has_employer = raw_employers.notna() & raw_employers.astype(str).str.strip().ne("")
    missing_employer = reclassified & (
        df["contributor_employer"].isna()
        | df["contributor_employer"].eq("NOT DISCLOSED")
    )
    restore = has_employer & missing_employer.loc[has_employer.index]
    if not restore.any():
        return

    restored, _ = _normalize_text(
        raw_employers[restore],
        EMPLOYER_NORMALIZE,
    )
    restored = restored[restored.notna() & ~restored.isin(MISSING_VALUES)]
    if not restored.empty:
        df.loc[restored.index, "contributor_employer"] = restored


def _clear_individual_residue(df: pd.DataFrame) -> None:
    is_individual = df["is_individual"]
    df.loc[is_individual, "committee_type"] = "NOT_APPLICABLE"

    missing_status = is_individual & df["occupation_status"].eq("NOT_APPLICABLE")
    if missing_status.any():
        df.loc[missing_status, "occupation_status"] = "MISSING"

    missing_occupation = is_individual & df["contributor_occupation"].isna()
    if missing_occupation.any():
        df.loc[missing_occupation, "occupation_category"] = pd.NA
        df.loc[missing_occupation, "occupation_status"] = "MISSING"

    short_fields = (
        ("contributor_employer", OK_SHORT_EMPLOYERS),
        ("contributor_occupation", OK_SHORT_OCCUPATIONS),
    )
    for column, whitelist in short_fields:
        values = df[column]
        junk = (
            is_individual
            & values.notna()
            & values.str.len().le(2)
            & ~values.isin(whitelist)
        )
        if junk.any():
            df.loc[junk, column] = np.nan


def _restore_reclassified_committees(
    df: pd.DataFrame,
    raw_occ_backup: pd.Series,
    raw_emp_backup: pd.Series,
    log,
    clear_residue: bool = True,
) -> None:
    """Restore work fields for committee rows reclassified as individuals."""
    reclassified = df["is_individual"] & df["_reclass_reason"].str.startswith(
        "committee_to_individual", na=False
    )
    if reclassified.any():
        raw_occupations = raw_occ_backup.loc[reclassified]
        raw_employers = raw_emp_backup.loc[reclassified]
        _restore_individual_occupations(df, raw_occupations, raw_employers, log)
        _restore_individual_employers(df, reclassified, raw_employers)
    if clear_residue:
        _clear_individual_residue(df)
