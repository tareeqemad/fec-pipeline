"""The columns of the cleaned file, and the checks each stage runs on a file's columns."""

from collections.abc import Iterable

# clean.py writes exactly these, in this order
CLEAN_COLUMNS = (
    "sub_id",
    "transaction_id",
    "two_year_transaction_period",
    "recipient_committee",
    # who the donor is, from donor matching
    "donor_key",
    "identity_status",
    "entity_type",
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
    "occupation_category",
    "contribution_receipt_date",
    "contribution_receipt_amount",
    "previous_employer",
)

DONOR_COORDINATES = ("latitude", "longitude")

# what the loader reads: the file after the last stage
LOADED_COLUMNS = CLEAN_COLUMNS + DONOR_COORDINATES + ("employer_status",)


class ContractError(ValueError):
    """A file does not hold the columns its stage expects."""


def check_input(command: str, columns: Iterable[str], added_by_this_command: str | None = None) -> None:
    """Stop when a cleaned column is missing, or the file already went through this command."""
    columns = set(columns)
    if added_by_this_command in columns:
        raise ContractError(f"{command} already ran on this file ({added_by_this_command}); run clean.py first")
    missing = [column for column in CLEAN_COLUMNS if column not in columns]
    if missing:
        raise ContractError(f"{command} needs columns {', '.join(missing)}; run clean.py first")


def _describe_difference(columns: Iterable[str], expected: Iterable[str]) -> str:
    columns, expected = list(columns), set(expected)
    found = {
        "missing": sorted(expected - set(columns)),
        "unexpected": sorted(set(columns) - expected),
        "duplicated": sorted({column for column in columns if columns.count(column) > 1}),
    }
    return "; ".join(f"{problem} {', '.join(names)}" for problem, names in found.items() if names)


def check_output(command: str, columns: Iterable[str], expected: Iterable[str]) -> None:
    """Stop when the file to write does not hold exactly the expected columns, each once."""
    difference = _describe_difference(columns, expected)
    if difference:
        raise ContractError(f"{command} would write a broken file: {difference}")


def check_loadable(columns: Iterable[str]) -> None:
    """Stop when the file is not the finished one: cleaned, geocoded and resolved."""
    difference = _describe_difference(columns, LOADED_COLUMNS)
    if difference:
        raise ContractError(
            f"contributions_cleaned.csv is not the finished file: {difference}; "
            "run clean.py, geocode.py and resolve.py --apply"
        )
