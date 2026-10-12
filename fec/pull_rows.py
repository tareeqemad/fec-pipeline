"""Turn FEC API results into raw rows."""
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


# FEC source fields, never in raw file
SOURCE_FIELDS = {
    "fec_entity_type": "entity_type",
    "fec_contributor_id": "contributor_id",
    # the filing page: https://docquery.fec.gov/cgi-bin/fecimg/?<image_number>
    "fec_image_number": "image_number",
}

# the FEC API fields a pull requests
FIELDS = RAW_FIELDS + list(SOURCE_FIELDS.values())

# contributor_year preserves the existing raw CSV contract.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:]

# per sub_id; fec_sub_id set when amended
SOURCE_COLUMNS = ["sub_id", *SOURCE_FIELDS, "matched_by", "fec_sub_id"]


# parse a Decimal, zero on failure
def _number(value) -> Decimal:
    try:
        return Decimal(str(value).strip() or "0") if value is not None else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


# first ten chars as YYYY-MM-DD, else None
def _date(value) -> str | None:
    return str(value)[:10] if value and str(value).strip() else None


# year from a date string, else None
def _year(date_value: str | None) -> int | None:
    try:
        return int(date_value[:4]) if date_value else None
    except ValueError:
        return None


# raw CSV row, or None without sub_id
def build_row(result: dict[str, Any]) -> list | None:
    sub_id = str(result.get("sub_id") or "").strip()
    if not sub_id:
        return None

    receipt_date = _date(result.get("contribution_receipt_date"))
    values = {field: result.get(field) for field in RAW_FIELDS}
    values["sub_id"] = sub_id
    values["contribution_receipt_date"] = receipt_date
    values["contributor_year"] = _year(receipt_date)
    values["contribution_receipt_amount"] = _number(
        result.get("contribution_receipt_amount")
    )
    return [values.get(column) for column in COLUMNS]


# one result's source fields by raw column
def source_values(result: dict[str, Any]) -> dict[str, str]:
    return {
        column: str(result.get(field) or "").strip().upper()
        for column, field in SOURCE_FIELDS.items()
    }
