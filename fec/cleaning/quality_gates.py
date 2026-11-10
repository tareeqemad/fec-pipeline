"""Checks the cleaned data must pass before it is saved.

Each check names what must hold and returns the filings that break it.
A check that needs a column the stage has not written yet (employer_status
before resolve) is skipped and listed as not run.
"""

import pandas as pd

from fec.cleaning.addresses.donor_addresses import zip_place
from fec.cleaning.filing_corrections import categorize_with_corrections
from fec.cleaning.work.employer_status import classify_employer_statuses, is_real_employer
from fec.vocabulary.occupations import VALID_CATEGORIES


def _read_text(df: pd.DataFrame, column: str) -> pd.Series:
    """Return a column as stripped text, with blanks for missing values."""
    return df[column].fillna("").astype(str).str.strip()


def _find_people(df: pd.DataFrame) -> pd.Series:
    """Return which rows are individuals."""
    return df["entity_type"].eq("INDIVIDUAL")


def _find_nan_text(df: pd.DataFrame) -> pd.Series:
    """Return the rows where a text column holds the word NAN."""
    columns = (
        "contributor_occupation",
        "contributor_employer",
        "contributor_name",
        "contributor_city",
        "contributor_state",
        "contributor_street_1",
    )
    return pd.concat([df[column].astype("string").str.upper().eq("NAN") for column in columns], axis=1).any(axis=1)


def _find_unreadable_amount(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose amount is not a number."""
    return pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").isna()


def _find_unknown_category(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose occupation category is not a valid one."""
    return ~_read_text(df, "occupation_category").isin(VALID_CATEGORIES)


def _find_category_differs(df: pd.DataFrame) -> pd.Series:
    """Return the individuals whose category differs from the one the rules give."""
    people = _find_people(df)
    filed = _read_text(df, "occupation_category")[people]
    differs = filed != categorize_with_corrections(df[people])
    return differs.reindex(df.index, fill_value=False)


def _find_status_differs(df: pd.DataFrame) -> pd.Series:
    """Return the individuals whose employer status differs from the classified one."""
    return _find_people(df) & _read_text(df, "employer_status").ne(classify_employer_statuses(df))


def _find_previous_employer_not_retired(df: pd.DataFrame) -> pd.Series:
    """Return the rows with a previous employer whose status is not retired."""
    has_previous = _read_text(df, "previous_employer").ne("")
    return has_previous & _read_text(df, "employer_status").ne("retired")


def _find_not_an_employer(df: pd.DataFrame) -> pd.Series:
    """Return the individuals whose current or previous employer is not a real employer."""
    status = _read_text(df, "employer_status")
    current, previous = _read_text(df, "contributor_employer"), _read_text(df, "previous_employer")
    working = _find_people(df) & (status.eq("active") | (status.eq("self_employed") & current.ne("SELF-EMPLOYED")))
    retired = _find_people(df) & status.eq("retired") & previous.ne("")
    real = {name: is_real_employer(name) for name in set(current[working]) | set(previous[retired])}
    current_is_real = current.map(real).fillna(True).astype(bool)
    previous_is_real = previous.map(real).fillna(True).astype(bool)
    return (working & ~current_is_real) | (retired & ~previous_is_real)


def _find_no_zip(df: pd.DataFrame) -> pd.Series:
    """Return the rows without a ZIP."""
    return _read_text(df, "contributor_zip").eq("")


def _find_duplicate_sub_id(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose sub_id an earlier row already has."""
    return df["sub_id"].duplicated()


def _find_duplicate_transaction_id(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose transaction_id an earlier row already has."""
    return df["transaction_id"].duplicated()


def _find_dated_ahead(df: pd.DataFrame) -> pd.Series:
    """Return the rows dated more than 60 days from now."""
    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    return dates > pd.Timestamp.now() + pd.Timedelta(days=60)


def _read_zip_state(zipcode: str) -> str:
    """Return the state a ZIP belongs to, or '' when it is not known."""
    return zip_place(zipcode)[0]


def _find_zip_of_another_state(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose ZIP belongs to a different state."""
    zip_state = _read_text(df, "contributor_zip").map(_read_zip_state)
    filed_state = _read_text(df, "contributor_state")
    return zip_state.ne("") & filed_state.ne("") & zip_state.ne(filed_state)


def _find_email_street(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose street holds an @."""
    return _read_text(df, "contributor_street_1").str.contains("@", regex=False)


def _find_odd_name_characters(df: pd.DataFrame) -> pd.Series:
    """Return the rows whose first or last name holds ` ; [ ] or @."""
    first = _read_text(df, "contributor_first_name").str.contains(r"[`;\[\]@]")
    last = _read_text(df, "contributor_last_name").str.contains(r"[`;\[\]@]")
    return first | last


def _find_retired_but_active(df: pd.DataFrame) -> pd.Series:
    """Return the individuals with a RETIRED category and an active employer status."""
    return _find_people(df) & df["occupation_category"].eq("RETIRED") & df["employer_status"].eq("active")


def _find_retired_without_retired_employer(df: pd.DataFrame) -> pd.Series:
    """Return the individuals with a RETIRED category whose employer is not RETIRED."""
    return (
        _find_people(df)
        & df["occupation_category"].eq("RETIRED")
        & _read_text(df, "contributor_employer").ne("RETIRED")
    )


def _find_null_surname_lost(df: pd.DataFrame) -> pd.Series:
    """Return the rows filed with the surname NULL whose last name went missing."""
    # NULL is a real surname; a reader that turns it into a missing value loses it
    filed_null = df["contributor_name"].astype("string").str.startswith("NULL,", na=False)
    return filed_null & _read_text(df, "contributor_last_name").eq("")


# What must hold, the filings that break it, and how many may.
CHECKS = (
    ("no text column holds the word NAN", _find_nan_text, 0),
    ("every contribution has a readable amount", _find_unreadable_amount, 0),
    ("every occupation category is a known one", _find_unknown_category, 0),
    ("the occupation category is the one of the occupation", _find_category_differs, 0),
    ("employer_status is the one the work fields give", _find_status_differs, 0),
    (
        "only a retired filing has a previous employer",
        _find_previous_employer_not_retired,
        0,
    ),
    ("an employer or previous employer names an employer", _find_not_an_employer, 0),
    ("at least half the filings have a ZIP", _find_no_zip, 0.5),
    ("sub_id is unique", _find_duplicate_sub_id, 0),
    ("transaction_id is unique", _find_duplicate_transaction_id, 0),
    ("no contribution is dated more than 60 days ahead", _find_dated_ahead, 0),
    ("the ZIP belongs to the state", _find_zip_of_another_state, 99),
    ("no street is an email address", _find_email_street, 0),
    ("no name holds ` ; [ ] or @", _find_odd_name_characters, 0),
    ("a RETIRED category is never an active employer", _find_retired_but_active, 0),
    (
        "a RETIRED category has RETIRED as the employer",
        _find_retired_without_retired_employer,
        0,
    ),
    ("the surname NULL is kept", _find_null_surname_lost, 0),
)


def run_quality_gates(df: pd.DataFrame) -> dict:
    """Run every check and return the result.

    The result is {'passed', 'rows', 'checks': {what must hold: filings that break it}, 'issues'}.
    """
    checks, issues = {}, []
    for must_hold, breaking, allowed in CHECKS:
        try:
            count = int(breaking(df).sum())
        except KeyError as missing:
            checks[must_hold] = f"not run: no column {missing}"
            continue
        checks[must_hold] = count
        limit = allowed * len(df) if isinstance(allowed, float) else allowed
        if count > limit:
            issues.append(f"{must_hold}: {count:,} filing(s) do not")
    return {"passed": not issues, "rows": int(len(df)), "checks": checks, "issues": issues}
