"""Address review queues and safe street_2 fixes."""

from pathlib import Path

import pandas as pd

from fec.cleaning.addresses.street_reviews import (
    REPORT_COLUMNS,
    CITY,
    S1,
    S2,
    STATE,
    ZIP,
    near_street_variant_review,
    review_street1,
    review_street2,
)
from fec.io import write_csv_atomic

MANUAL_REVIEW_CSV = "address_manual_review.csv"
REGEOCODE_CSV = "address_regeocode_suspects.csv"
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


# empty bad street_2 values; module's only edit
def apply_street2_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    s2 = df[S2].fillna("").astype(str)
    reports, emptied = review_street2(df, s2)
    return df, _combine_reports(reports), emptied


# keep auto-fixed rows not later restored
def _still_emptied(auto_fixed: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    if auto_fixed.empty or "sub_id" not in auto_fixed.columns or "sub_id" not in df.columns:
        return auto_fixed
    final_s2 = df.drop_duplicates("sub_id").set_index("sub_id")[S2]
    final_s2 = final_s2.fillna("").astype(str).str.strip()
    now = auto_fixed["sub_id"].map(final_s2)
    before = auto_fixed[S2].fillna("").astype(str).str.strip()
    return auto_fixed[now.notna() & now.ne(before)]


# attach final donor_key and entity_type to rows
def _add_donor_key(report: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    if report.empty or "sub_id" not in report.columns or "sub_id" not in df.columns:
        return report
    final = df.drop_duplicates("sub_id").set_index("sub_id")
    report = report.copy()
    for column in ("donor_key", "entity_type"):
        if column in final.columns and column not in report.columns:
            report[column] = report["sub_id"].map(final[column])
    order = ["review_reason"] + [c for c in REPORT_COLUMNS if c in report.columns]
    return report[order + [c for c in report.columns if c not in order]]


# build review and regeocode queues; never edits
def build_review_queues(
    df: pd.DataFrame,
    auto_fixed: pd.DataFrame | None = None,
    exclude_sub_ids=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if exclude_sub_ids is not None and len(exclude_sub_ids) and "sub_id" in df.columns:
        df = df[~df["sub_id"].isin(set(exclude_sub_ids))]
        if auto_fixed is not None and not auto_fixed.empty and "sub_id" in auto_fixed.columns:
            auto_fixed = auto_fixed[~auto_fixed["sub_id"].isin(set(exclude_sub_ids))]

    s1 = df[S1].fillna("").astype(str)
    city = df[CITY].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)
    zips = df[ZIP].fillna("").astype(str)

    review = []
    spelling_variants = near_street_variant_review(df)
    if not spelling_variants.empty:
        review.append(spelling_variants)
    street1_review, regeocode = review_street1(df, s1, city, state, zips)
    review.extend(street1_review)

    queue = [_with_status(_combine_reports(review), OPEN)]
    if auto_fixed is not None and not auto_fixed.empty:
        fixed = _add_donor_key(_still_emptied(auto_fixed, df), df)
        queue.append(_with_status(fixed, AUTO_FIXED))
    review_df = _combine_reports([part for part in queue if not part.empty])
    return review_df, _combine_reports(regeocode)


# write the two review queues to CSV
def write_review_queues(out_dir, review_df: pd.DataFrame, regeocode_df: pd.DataFrame) -> None:
    out_path = Path(out_dir)
    write_csv_atomic(review_df, out_path / MANUAL_REVIEW_CSV, index=False, na_rep="")
    write_csv_atomic(regeocode_df, out_path / REGEOCODE_CSV, index=False, na_rep="")


# summarize row counts per review queue status
def queue_counts(review_df: pd.DataFrame, regeocode_df: pd.DataFrame) -> dict:
    status = review_df["status"] if "status" in review_df.columns else pd.Series(dtype=object)
    return {
        "manual_review": int(status.eq(OPEN).sum()),
        "auto_fixed": int(status.eq(AUTO_FIXED).sum()),
        "regeocode": len(regeocode_df),
    }


# fix street_2, then build review reports
def build_address_reports(
    df: pd.DataFrame, out_dir: str | None
) -> tuple[pd.DataFrame, dict]:
    df, auto_fixed, street2_emptied = apply_street2_fixes(df)
    review_df, regeocode_df = build_review_queues(df, auto_fixed)
    counts = {**queue_counts(review_df, regeocode_df), "street2_emptied": street2_emptied}

    if out_dir:
        write_review_queues(out_dir, review_df, regeocode_df)

    return df, counts
