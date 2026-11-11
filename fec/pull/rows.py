"""Turn one FEC API result into one row of contributions.csv and one of fec_source_fields.csv."""

from decimal import Decimal, InvalidOperation

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

# FEC fields saved separately in fec_source_fields.csv: the column there, and the API field it holds.
SOURCE_FIELDS = {
    "fec_entity_type": "entity_type",
    "fec_contributor_id": "contributor_id",
    # the filing page: https://docquery.fec.gov/cgi-bin/fecimg/?<image_number>
    "fec_image_number": "image_number",
}

# The fields asked of the API.
FIELDS = RAW_FIELDS + list(SOURCE_FIELDS.values())

# The raw CSV columns: the API fields, with the year of the receipt date before the amount.
COLUMNS = RAW_FIELDS[:-1] + ["contributor_year"] + RAW_FIELDS[-1:]

# matched_by is the match method; fec_sub_id an amendment's ID.
SOURCE_COLUMNS = ["sub_id", *SOURCE_FIELDS, "matched_by", "fec_sub_id"]


def _read_amount(value: object, sub_id: str) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except InvalidOperation:
        amount = Decimal("NaN")
    if not amount.is_finite():
        raise ValueError(f"Invalid contribution_receipt_amount for sub_id {sub_id}: {value!r}")
    return amount


def build_row(sub_id: str, result: dict) -> list:
    """Build the raw CSV row of one filing: date without time, its year, and a checked amount."""
    values = {field: result.get(field) for field in RAW_FIELDS}
    values["sub_id"] = sub_id
    filed_date = str(result.get("contribution_receipt_date") or "")
    values["contribution_receipt_date"] = filed_date[:10] if filed_date.strip() else None
    values["contributor_year"] = int(filed_date[:4]) if filed_date[:4].isdecimal() else None
    values["contribution_receipt_amount"] = _read_amount(result.get("contribution_receipt_amount"), sub_id)
    return [values[column] for column in COLUMNS]


def build_source_row(sub_id: str, result: dict) -> dict:
    """Build the fec_source_fields.csv row of one pulled filing."""
    source_row = {"sub_id": sub_id, "matched_by": "pulled", "fec_sub_id": ""}
    for column, field in SOURCE_FIELDS.items():
        source_row[column] = str(result.get(field) or "").strip().upper()
    return source_row
