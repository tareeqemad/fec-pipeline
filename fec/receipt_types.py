"""Classify itemized receipts using the filed F3X line and memo flag."""
from pathlib import Path

import pandas as pd

from fec.io import write_csv_atomic
from fec.pull_rows import LINE_FIELDS


F3X_LINES = {
    "11AI": "contribution", "11B": "contribution", "11C": "contribution",
    "12": "other_receipt", "13": "other_receipt", "14": "other_receipt",
    "15": "operating_offset", "16": "contribution_refund", "17": "other_receipt",
}
RECEIPT_KINDS = frozenset(F3X_LINES.values()) | {"memo", "unclassified"}


def classify_receipt(form: str, line: str, memo: str) -> str:
    if memo.strip().upper() == "X":
        return "memo"
    if form.strip().upper() == "F3X" and not memo.strip():
        return F3X_LINES.get(line.strip().upper(), "unclassified")
    return "unclassified"


def classify_receipts(df: pd.DataFrame) -> pd.Series:
    if "receipt_kind" in df:
        saved = df["receipt_kind"].fillna("unclassified").replace("", "unclassified")
        if not saved.isin(RECEIPT_KINDS).all():
            raise ValueError("Invalid saved receipt_kind")
        return saved
    if not set(LINE_FIELDS).issubset(df.columns):
        return pd.Series("unclassified", index=df.index)
    fields = df[list(LINE_FIELDS)].astype("string").fillna("")
    kinds = (classify_receipt(*row) for row in fields.itertuples(index=False, name=None))
    return pd.Series(kinds, index=df.index, dtype=object)


def write_receipt_reports(df: pd.DataFrame, raw: pd.DataFrame, out_dir: str | Path) -> None:
    scope = ["recipient_committee", "two_year_transaction_period"]
    totals = df.groupby([*scope, "receipt_kind"], as_index=False).agg(
        receipts=("sub_id", "size"), amount=("contribution_receipt_amount", "sum")
    )
    write_csv_atomic(totals, Path(out_dir) / "receipt_totals.csv", index=False, float_format="%.2f")
    columns = ["sub_id", *scope, "transaction_id", "contribution_receipt_date", "contribution_receipt_amount"]
    pending = df.loc[df["receipt_kind"].eq("unclassified"), columns]
    source = raw.reindex(columns=["sub_id", "fec_image_number", "fec_filing_form", "fec_line_number", "fec_memo_code"])
    pending = pending.merge(source, on="sub_id", how="left", validate="one_to_one")
    write_csv_atomic(pending, Path(out_dir) / "receipt_lines_to_review.csv", index=False, float_format="%.2f")
