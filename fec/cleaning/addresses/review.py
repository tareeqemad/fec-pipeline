"""Write address review reports from the final cleaned records."""

from pathlib import Path

import pandas as pd

from fec.cleaning.addresses.review_cases import REVIEW_CASES_CSV, build_review_cases
from fec.cleaning.addresses.street_reviews import (
    _REPORT_COLUMNS,
    S1,
    S2,
    STATE,
    _near_street_variant_review,
    _review_street1,
    _review_street2,
)
from fec.io import write_csv_atomic
from fec.log import get_logger

logger = get_logger(__name__)

OPEN, AUTO_FIXED = "open", "auto_fixed"


# concatenate report fragments into one dataframe
def _combine_reports(reports: list) -> pd.DataFrame:
    return pd.concat(reports, ignore_index=True) if reports else pd.DataFrame()


# insert a status column into a report
def _with_status(report: pd.DataFrame, status: str) -> pd.DataFrame:
    if report.empty:
        return report
    report = report.copy()
    report.insert(1, "status", status)
    return report


# Clear invalid second lines; return frame, affected rows, count.
def apply_street2_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    s2 = df[S2].fillna("").astype(str)
    reports, emptied = _review_street2(df, s2)
    return df, _combine_reports(reports), emptied


def _final_auto_fixed(auto_fixed: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Drop restored street_2 rows; attach final donor details."""
    if auto_fixed.empty or "sub_id" not in auto_fixed.columns or "sub_id" not in df.columns:
        return auto_fixed
    final = df.drop_duplicates("sub_id").set_index("sub_id")
    final_s2 = final[S2].fillna("").astype(str).str.strip()
    now = auto_fixed["sub_id"].map(final_s2)
    before = auto_fixed[S2].fillna("").astype(str).str.strip()
    report = auto_fixed[now.notna() & now.ne(before)]
    if report.empty:
        return report
    report = report.copy()
    for column in ("donor_key", "entity_type"):
        if column in final.columns and column not in report.columns:
            report[column] = report["sub_id"].map(final[column])
    order = ["review_reason"] + [c for c in _REPORT_COLUMNS if c in report.columns]
    return report[order + [c for c in report.columns if c not in order]]


# Build the review queue without editing rows.
def build_review_queues(
    df: pd.DataFrame,
    auto_fixed: pd.DataFrame | None = None,
    exclude_sub_ids=None,
) -> pd.DataFrame:
    if exclude_sub_ids is not None and len(exclude_sub_ids) and "sub_id" in df.columns:
        df = df[~df["sub_id"].isin(set(exclude_sub_ids))]
        if auto_fixed is not None and not auto_fixed.empty and "sub_id" in auto_fixed.columns:
            auto_fixed = auto_fixed[~auto_fixed["sub_id"].isin(set(exclude_sub_ids))]

    s1 = df[S1].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)

    review = []
    spelling_variants = _near_street_variant_review(df)
    if not spelling_variants.empty:
        review.append(spelling_variants)
    review.extend(_review_street1(df, s1, state))

    queue = [_with_status(_combine_reports(review), OPEN)]
    if auto_fixed is not None and not auto_fixed.empty:
        fixed = _final_auto_fixed(auto_fixed, df)
        queue.append(_with_status(fixed, AUTO_FIXED))
    return _combine_reports([part for part in queue if not part.empty])


# Write reports after donor repairs and foreign-address restoration.
def write_final_address_reports(
    df_clean, out_dir, address_reports: dict, foreign_sub_ids, filed_addresses, trail,
) -> None:
    review_df = build_review_queues(
        df_clean,
        address_reports.get("street2_auto_fixed"),
        exclude_sub_ids=foreign_sub_ids,
    )
    cases = build_review_cases(review_df, df_clean, filed_addresses, trail.net_records())
    write_csv_atomic(cases, Path(out_dir) / REVIEW_CASES_CSV, index=False)
    if not cases.empty:
        high_priority = int(cases['priority'].eq('high').sum())
        logger.info(
            "Review: %s address cases (%s high priority)",
            f"{len(cases):,}", f"{high_priority:,}",
        )
