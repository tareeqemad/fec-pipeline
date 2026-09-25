"""Address hygiene beside clean_streets: safe mechanical text fixes are applied; anything needing a guess goes to the review reports.

Two review files, both regenerated every clean run (nothing reads them back):

* address_manual_review.csv - one row per item, with a ``status`` column:
    ``open``        needs a human (near-duplicate street spellings, care-of or
                    entity names in street_1, descriptive addresses, non-US state);
    ``auto_fixed``  a street_2 the address_review step already emptied (a unit
                    word with no number, a state/city abbreviation, a state/ZIP/city
                    fragment). Listed after the open items so the removal stays
                    visible; the row shows the address as the step saw it, with
                    street_2 as it was before it was emptied.
* address_regeocode_suspects.csv - rows whose point can only be approximate:
  missing street_1 or ZIP, a truncated city, and PO Box / PMB mail addresses of
  INDIVIDUAL donors. Committee and organization mailboxes are not listed: they
  are normal for a filer's office address and the geocoder already places every
  PO box at its ZIP/city point.

In the clean pipeline the street_2 fix runs inside the address stage, and the files
are written after the donor stage (``build_review_queues`` + ``write_review_queues``)
so they reflect the final rows and carry donor_key; foreign filings, kept exactly
as filed, are left out.
"""

from pathlib import Path

import pandas as pd

from fec.cleaning.addresses.street_reviews import (
    _REPORT_COLUMNS,
    CITY,
    S1,
    S2,
    STATE,
    ZIP,
    _near_street_variant_review,
    _review_street1,
    _review_street2,
)

MANUAL_REVIEW_CSV = "address_manual_review.csv"
REGEOCODE_CSV = "address_regeocode_suspects.csv"
OPEN, AUTO_FIXED = "open", "auto_fixed"


# Bare trailing number -> unit. Excludes HWY/RTE so route numbers
# ("HWY 9", "RTE 1") aren't mistaken for unit numbers.


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


# empty clearly bad street_2 values, the module's only edit
def apply_street2_fixes(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Empty clearly bad street_2 values (the only edit of the address_review step).

    Returns the frame, the emptied rows as they were before the edit (reason,
    sub_id, address with the original street_2) and the number of rows emptied.
    """
    s2 = df[S2].fillna("").astype(str)
    reports, emptied = _review_street2(df, s2)
    return df, _combine_reports(reports), emptied


# keep only auto-fixed rows whose street_2 wasn't later restored
def _still_emptied(auto_fixed: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Drop auto-fixed rows whose street_2 a later step put back (a foreign filing restored as filed)."""
    if auto_fixed.empty or "sub_id" not in auto_fixed.columns or "sub_id" not in df.columns:
        return auto_fixed
    final_s2 = df.drop_duplicates("sub_id").set_index("sub_id")[S2]
    final_s2 = final_s2.fillna("").astype(str).str.strip()
    now = auto_fixed["sub_id"].map(final_s2)
    before = auto_fixed[S2].fillna("").astype(str).str.strip()
    return auto_fixed[now.notna() & now.ne(before)]


# attach the final donor_key/entity_type to a report's rows
def _add_donor_key(report: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Attach the final donor_key (and entity_type) to rows captured before donors were identified."""
    if report.empty or "sub_id" not in report.columns or "sub_id" not in df.columns:
        return report
    final = df.drop_duplicates("sub_id").set_index("sub_id")
    report = report.copy()
    for column in ("donor_key", "entity_type"):
        if column in final.columns and column not in report.columns:
            report[column] = report["sub_id"].map(final[column])
    order = ["review_reason"] + [c for c in _REPORT_COLUMNS if c in report.columns]
    return report[order + [c for c in report.columns if c not in order]]


# build the manual-review and regeocode queues from the frame
def build_review_queues(
    df: pd.DataFrame,
    auto_fixed: pd.DataFrame | None = None,
    exclude_sub_ids=None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the manual-review and re-geocode queues from ``df``; never edits it.

    ``auto_fixed`` holds the street_2 values the address_review step emptied
    (from ``apply_street2_fixes``); they are appended with status auto_fixed.
    ``exclude_sub_ids`` (foreign filings kept as filed) are left out.
    """
    if exclude_sub_ids is not None and len(exclude_sub_ids) and "sub_id" in df.columns:
        df = df[~df["sub_id"].isin(set(exclude_sub_ids))]
        if auto_fixed is not None and not auto_fixed.empty and "sub_id" in auto_fixed.columns:
            auto_fixed = auto_fixed[~auto_fixed["sub_id"].isin(set(exclude_sub_ids))]

    s1 = df[S1].fillna("").astype(str)
    city = df[CITY].fillna("").astype(str)
    state = df[STATE].fillna("").astype(str)
    zips = df[ZIP].fillna("").astype(str)

    review = []
    spelling_variants = _near_street_variant_review(df)
    if not spelling_variants.empty:
        review.append(spelling_variants)
    street1_review, regeocode = _review_street1(df, s1, city, state, zips)
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
    review_df.to_csv(out_path / MANUAL_REVIEW_CSV, index=False, na_rep="")
    regeocode_df.to_csv(out_path / REGEOCODE_CSV, index=False, na_rep="")


# summarize row counts per review queue status
def queue_counts(review_df: pd.DataFrame, regeocode_df: pd.DataFrame) -> dict:
    status = review_df["status"] if "status" in review_df.columns else pd.Series(dtype=object)
    return {
        "manual_review": int(status.eq(OPEN).sum()),
        "auto_fixed": int(status.eq(AUTO_FIXED).sum()),
        "regeocode": len(regeocode_df),
    }


# run the street_2 fix and build review reports at once
def build_address_reports(
    df: pd.DataFrame, out_dir: str | None
) -> tuple[pd.DataFrame, dict]:
    """Empty clearly bad street_2 values, then flag questionable addresses (one-shot form)."""
    df, auto_fixed, street2_emptied = apply_street2_fixes(df)
    review_df, regeocode_df = build_review_queues(df, auto_fixed)
    counts = {**queue_counts(review_df, regeocode_df), "street2_emptied": street2_emptied}

    if out_dir:
        write_review_queues(out_dir, review_df, regeocode_df)

    return df, counts
