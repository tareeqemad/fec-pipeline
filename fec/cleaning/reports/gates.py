"""Checks the cleaned data must pass before it is saved.

Each check names what must hold and returns the filings that break it.
A check that needs a column the stage has not written yet (employer_status
before resolve) is skipped and listed as not run.
"""
import pandas as pd

from fec.cleaning.addresses.align import _zip_place
from fec.cleaning.rules.manual import categorize_records
from fec.cleaning.work.status import classify_employer_statuses, is_real_employer
from fec.config.occupation_rules.categories import VALID_CATEGORIES


def _text(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column].fillna("").astype(str).str.strip()


def _people(df: pd.DataFrame) -> pd.Series:
    return df["entity_type"].eq("INDIVIDUAL")


def _nan_text(df):
    columns = ("contributor_occupation", "contributor_employer", "contributor_name", "contributor_city",
               "contributor_state", "contributor_street_1")
    return pd.concat([df[column].astype("string").str.upper().eq("NAN") for column in columns], axis=1).any(axis=1)


def _unknown_category(df):
    return ~_text(df, "occupation_category").isin(VALID_CATEGORIES)


def _category_differs(df):
    people = _people(df)
    return (_text(df, "occupation_category")[people] != categorize_records(df[people])).reindex(df.index, fill_value=False)


def _status_differs(df):
    return _people(df) & _text(df, "employer_status").ne(classify_employer_statuses(df))


def _not_an_employer(df):
    status = _text(df, "employer_status")
    current, previous = _text(df, "contributor_employer"), _text(df, "previous_employer")
    working = _people(df) & (status.eq("active") | (status.eq("self_employed") & current.ne("SELF-EMPLOYED")))
    retired = _people(df) & status.eq("retired") & previous.ne("")
    real = {name: is_real_employer(name) for name in set(current[working]) | set(previous[retired])}
    return (working & ~current.map(real).fillna(True).astype(bool)) | (retired & ~previous.map(real).fillna(True).astype(bool))


def _zip_of_another_state(df):
    state = _text(df, "contributor_zip").map(lambda zipcode: _zip_place(zipcode)[0])
    return state.ne("") & _text(df, "contributor_state").ne("") & state.ne(_text(df, "contributor_state"))


def _null_surname_lost(df):
    # NULL is a real surname; a reader that turns it into a missing value loses it
    return df["contributor_name"].astype("string").str.startswith("NULL,", na=False) & _text(df, "contributor_last_name").eq("")


# What must hold, the filings that break it, and how many may.
CHECKS = (
    ("no text column holds the word NAN", _nan_text, 0),
    ("every contribution has a readable amount",
     lambda df: pd.to_numeric(df["contribution_receipt_amount"], errors="coerce").isna(), 0),
    ("every occupation category is a known one", _unknown_category, 0),
    ("the occupation category is the one of the occupation", _category_differs, 0),
    ("employer_status is the one the work fields give", _status_differs, 0),
    ("only a retired filing has a previous employer",
     lambda df: _text(df, "previous_employer").ne("") & _text(df, "employer_status").ne("retired"), 0),
    ("an employer or previous employer names an employer", _not_an_employer, 0),
    ("at least half the filings have a ZIP", lambda df: _text(df, "contributor_zip").eq(""), 0.5),
    ("sub_id is unique", lambda df: df["sub_id"].duplicated(), 0),
    ("transaction_id is unique", lambda df: df["transaction_id"].duplicated(), 0),
    ("no contribution is dated more than 60 days ahead",
     lambda df: pd.to_datetime(df["contribution_receipt_date"], errors="coerce") > pd.Timestamp.now() + pd.Timedelta(days=60), 0),
    ("the ZIP belongs to the state", _zip_of_another_state, 99),
    ("no street is an email address", lambda df: _text(df, "contributor_street_1").str.contains("@", regex=False), 0),
    ("no name holds ` ; [ ] or @",
     lambda df: _text(df, "contributor_first_name").str.contains(r"[`;\[\]@]") | _text(df, "contributor_last_name").str.contains(r"[`;\[\]@]"), 0),
    ("a RETIRED category is never an active employer",
     lambda df: _people(df) & df["occupation_category"].eq("RETIRED") & df["employer_status"].eq("active"), 0),
    ("a RETIRED category has RETIRED as the employer",
     lambda df: _people(df) & df["occupation_category"].eq("RETIRED") & _text(df, "contributor_employer").ne("RETIRED"), 0),
    ("the surname NULL is kept", _null_surname_lost, 0),
)


def run_quality_gates(df: pd.DataFrame) -> dict:
    """Run every check; {'passed', 'rows', 'checks': {what must hold: filings that break it}, 'issues'}."""
    checks, issues = {}, []
    for must_hold, breaking, allowed in CHECKS:
        try:
            count = int(breaking(df).sum())
        except KeyError as missing:
            checks[must_hold] = f"not run: no column {missing}"
            continue
        checks[must_hold] = count
        if count > (allowed * len(df) if isinstance(allowed, float) else allowed):
            issues.append(f"{must_hold}: {count:,} filing(s) do not")
    return {"passed": not issues, "rows": int(len(df)), "checks": checks, "issues": issues}
