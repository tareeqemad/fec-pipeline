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


# What FEC itself says about the contributor, kept apart from what clean
# decides: raw column -> FEC API field. entity_type is IND, ORG, COM, PAC,
# PTY, CCM or CAN; contributor_id is the FEC id of a committee contributor.
SOURCE_FIELDS = {
    "fec_entity_type": "entity_type",
    "fec_contributor_id": "contributor_id",
}

# the FEC API fields one pull asks for
FIELDS = RAW_FIELDS + list(SOURCE_FIELDS.values())

# contributor_year preserves the existing raw CSV contract; the source
# fields come last so older files gain them as trailing columns.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:] + list(SOURCE_FIELDS)


# parse a value into a Decimal, default zero on failure
def _number(value) -> Decimal:
    try:
        return Decimal(str(value).strip() or "0") if value is not None else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


# truncate a value to a YYYY-MM-DD date string, else None
def _date(value) -> str | None:
    return str(value)[:10] if value and str(value).strip() else None


# extract the year from a date string, else None
def _year(date_value: str | None) -> int | None:
    try:
        return int(date_value[:4]) if date_value else None
    except ValueError:
        return None


# convert one FEC API result into a raw CSV row
def build_row(result: dict[str, Any]) -> list | None:
    """Convert one FEC result to the raw CSV schema."""
    sub_id = str(result.get("sub_id") or "").strip()
    if not sub_id:
        return None

    receipt_date = _date(result.get("contribution_receipt_date"))
    values = {field: result.get(field) for field in RAW_FIELDS}
    values.update(source_values(result))
    values["sub_id"] = sub_id
    values["contribution_receipt_date"] = receipt_date
    values["contributor_year"] = _year(receipt_date)
    values["contribution_receipt_amount"] = _number(
        result.get("contribution_receipt_amount")
    )
    return [values.get(column) for column in COLUMNS]


# the source fields of one FEC API result, by raw column
def source_values(result: dict[str, Any]) -> dict[str, str]:
    return {
        column: str(result.get(field) or "").strip().upper()
        for column, field in SOURCE_FIELDS.items()
    }
