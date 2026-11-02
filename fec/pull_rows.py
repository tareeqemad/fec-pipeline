"""Convert FEC Schedule A fields to the project's receipt CSV format."""
from __future__ import annotations

from datetime import date
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


# Contributor metadata retained in snapshots.
ENTITY_FIELDS = {
    "fec_entity_type": "entity_type",
    "fec_contributor_id": "contributor_id",
    # the filing page: https://docquery.fec.gov/cgi-bin/fecimg/?<image_number>
    "fec_image_number": "image_number",
}

LINE_FIELDS = {
    "fec_filing_form": "filing_form",
    "fec_line_number": "line_number",
    "fec_memo_code": "memo_code",
}
SOURCE_FIELDS = {**ENTITY_FIELDS, **LINE_FIELDS}
FIELDS = RAW_FIELDS + list(SOURCE_FIELDS.values()) + ["memoed_subtotal"]

# Keep the raw CSV column order.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:]
SNAPSHOT_COLUMNS = [*COLUMNS, *ENTITY_FIELDS, "receipt_kind"]

# matched_by is the match method; fec_sub_id an amendment's ID.
SOURCE_COLUMNS = ["sub_id", *ENTITY_FIELDS, "matched_by", "fec_sub_id"]


# Reject missing or invalid amounts instead of recording zero.
def _number(value, sub_id: str) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid contribution_receipt_amount for sub_id {sub_id}: {value!r}") from None
    if not amount.is_finite():
        raise ValueError(f"Invalid contribution_receipt_amount for sub_id {sub_id}: {value!r}")
    return amount


# Validate required fields before publishing the receipt.
def build_row(result: dict[str, Any]) -> list:
    sub_id = str(result.get("sub_id") or "").strip()
    if not sub_id.isascii() or not sub_id.isdigit():
        raise ValueError(f"Invalid Schedule A sub_id: {sub_id!r}")

    date_value = result.get("contribution_receipt_date")
    try:
        receipt_date = date.fromisoformat(str(date_value)[:10])
    except ValueError:
        raise ValueError(f"Invalid contribution_receipt_date for sub_id {sub_id}: {date_value!r}") from None
    values = {field: result.get(field) for field in RAW_FIELDS}
    values["sub_id"] = sub_id
    values["contribution_receipt_date"] = receipt_date.isoformat()
    values["contributor_year"] = receipt_date.year
    values["contribution_receipt_amount"] = _number(
        result.get("contribution_receipt_amount"), sub_id
    )
    return [values.get(column) for column in COLUMNS]


# Read the source fields from the same version of the receipt.
def source_values(result: dict[str, Any]) -> dict[str, str]:
    values = {
        column: str(result.get(field) or "").strip().upper()
        for column, field in SOURCE_FIELDS.items()
    }
    subtotal = str(result.get("memoed_subtotal", "")).lower()
    if subtotal in ("true", "t", "1"):
        values["fec_memo_code"] = "X"
    elif "memo_code" not in result and subtotal not in ("false", "f", "0"):
        values["fec_memo_code"] = "?"
    return values
