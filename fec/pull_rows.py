"""Turn one FEC API result into one row of contributions.csv."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

RAW_FIELDS = [
    "sub_id",
    "transaction_id",
    "two_year_transaction_period",
    "committee_id",
    "contributor_name",
    "contributor_first_name",
    "contributor_last_name",
    "contributor_street_1",
    "contributor_street_2",
    "contributor_city",
    "contributor_state",
    "contributor_zip",
    "contributor_employer",
    "contributor_occupation",
    "is_individual",
    "contribution_receipt_date",
    "contribution_receipt_amount",
]


FIELDS = RAW_FIELDS

# Keep the raw CSV column order.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:]


# Reject missing or invalid amounts instead of recording zero.
def _number(value, sub_id: str) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid contribution_receipt_amount for sub_id {sub_id}: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"Invalid contribution_receipt_amount for sub_id {sub_id}: {value!r}")
    return amount


# Build a raw CSV row, skipping filings without an ID.
def build_row(result: dict[str, Any]) -> list | None:
    sub_id = str(result.get("sub_id") or "").strip()
    if not sub_id:
        return None

    date_value = result.get("contribution_receipt_date")
    receipt_date = str(date_value)[:10] if date_value and str(date_value).strip() else None
    try:
        year = int(receipt_date[:4]) if receipt_date else None
    except ValueError:
        year = None
    values = {field: result.get(field) for field in RAW_FIELDS}
    values["sub_id"] = sub_id
    values["contribution_receipt_date"] = receipt_date
    values["contributor_year"] = year
    values["contribution_receipt_amount"] = _number(
        result.get("contribution_receipt_amount"), sub_id
    )
    return [values.get(column) for column in COLUMNS]
