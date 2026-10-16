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


# FEC fields saved separately in fec_source_fields.csv.
SOURCE_FIELDS = {
    "fec_entity_type": "entity_type",
    "fec_contributor_id": "contributor_id",
    # the filing page: https://docquery.fec.gov/cgi-bin/fecimg/?<image_number>
    "fec_image_number": "image_number",
}

FIELDS = RAW_FIELDS + list(SOURCE_FIELDS.values())

# Keep the raw CSV column order.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:]

# matched_by records the match method; fec_sub_id holds an amended filing's ID.
SOURCE_COLUMNS = ["sub_id", *SOURCE_FIELDS, "matched_by", "fec_sub_id"]


# Parse an exact amount; use zero for missing or invalid values.
def _number(value) -> Decimal:
    try:
        return Decimal(str(value).strip() or "0") if value is not None else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


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
        result.get("contribution_receipt_amount")
    )
    return [values.get(column) for column in COLUMNS]


# Read the FEC source fields saved beside the raw CSV.
def source_values(result: dict[str, Any]) -> dict[str, str]:
    return {
        column: str(result.get(field) or "").strip().upper()
        for column, field in SOURCE_FIELDS.items()
    }
