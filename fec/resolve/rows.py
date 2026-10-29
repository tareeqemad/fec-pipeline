"""Choose each filing's employer status and previous employer."""

from dataclasses import dataclass, field

import pandas as pd

from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.previous import normalize_previous_employer_value, preserve_own_named_legal_employer
from fec.cleaning.work.status import classify_employer_status
from fec.resolve.cache import _previous_for_row, _previous_employer_identity, _s
from fec.resolve.previous import PROTECTED_METHODS


@dataclass(frozen=True)
class ResolveContext:
    previous_cache: object
    # Row index to the donor's latest employer before that filing.
    dated_previous: dict = field(default_factory=dict)
    # sub_id to the employer the filing itself wrote.
    filed_employers: dict = field(default_factory=dict)
    # (donor_key, employer) to the first date filed.
    first_filed: dict = field(default_factory=dict)
    previous_display_names: dict = field(default_factory=dict)
    scoped_employer_rules: dict = field(default_factory=dict)


def _result(status: str, previous: str = "") -> dict:
    return {"employer_status": status, "previous_employer": previous}


def _display_previous_employer(
    prev_name: str, prev_entry: dict, donor_name: str
) -> str:
    sources = (
        prev_entry.get("employer_source", ""),
        prev_entry.get("employer", ""),
        prev_entry.get("employer_normalized", ""),
    )
    for source in sources:
        displayed = preserve_own_named_legal_employer(prev_name, source, donor_name)
        if displayed:
            return displayed
    return prev_name


def _first_filed_employers(df: pd.DataFrame) -> dict:
    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    working = df["entity_type"].eq("INDIVIDUAL") & dates.notna()
    employers = df.loc[working, "contributor_employer"].fillna("").astype(str).str.strip().str.upper()
    frame = pd.DataFrame({"donor": df.loc[working, "donor_key"], "employer": employers, "date": dates[working]})
    frame = frame[frame["employer"].ne("")]
    return frame.groupby(["donor", "employer"])["date"].min().to_dict()


def _learned_later(entry: dict, row: pd.Series, first_filed: dict) -> bool:
    """Whether the cached employer was learned after this filing."""
    filed = pd.to_datetime(row.get("contribution_receipt_date"), errors="coerce", utc=True)
    if pd.isna(filed):
        return False
    source = pd.to_datetime(entry.get("source_date"), errors="coerce", utc=True)
    if pd.notna(source):
        return source > filed
    donor = row.get("donor_key", "")
    names = {str(entry.get(key) or "").strip().upper() for key in ("employer", "employer_source")} - {""}
    firsts = [first_filed[(donor, name)] for name in names if (donor, name) in first_filed]
    return bool(firsts) and pd.to_datetime(min(firsts), utc=True) > filed


def _filed_previous(row: pd.Series, context: ResolveContext) -> dict | None:
    """The clean stage's previous employer when it is this filing's."""
    text = str(row.get("previous_employer") or "").strip()
    previous = normalize_previous_employer_value(text)
    filed = normalize_previous_employer_value(context.filed_employers.get(str(row.get("sub_id", "")), ""))
    filed = reviewed_employer_name(
        filed, row.get("donor_key", ""), row.get("two_year_transaction_period", ""),
        context.scoped_employer_rules if row.get("identity_status") == "confirmed" else None,
    )
    if previous and filed and canonical_key(previous) == canonical_key(filed):
        # Keep an own-named firm's legal form: AMY N DEAN PA.
        return {"employer": previous, "employer_source": text, "method": "filed"}
    return None


def _resolve_retired(
    row: pd.Series,
    context: ResolveContext,
    index=None,
) -> dict:
    """Reviewed history, then this filing, then dated history."""
    own_entry = _filed_previous(row, context)
    if row.get("identity_status", "confirmed") != "confirmed":
        prev_entry = own_entry
    else:
        prev_entry = _previous_for_row(context.previous_cache, row)
        protected = bool(prev_entry) and prev_entry.get("method") in PROTECTED_METHODS
        dated = context.dated_previous.get(index)
        if protected:
            pass
        elif own_entry:
            prev_entry = own_entry
        elif dated:
            prev_entry = {"employer": dated, "method": "cross_record"}
        elif prev_entry and _learned_later(prev_entry, row, context.first_filed):
            prev_entry = None
    prev_name, _keys = _previous_employer_identity(prev_entry)
    if not prev_name:
        return _result("retired")

    prev_name = _display_previous_employer(
        prev_name,
        prev_entry,
        row.get("contributor_name", ""),
    )
    # Rename established history.
    prev_name = context.previous_display_names.get(_s(row.get("sub_id")).strip(), prev_name)
    prev_name = reviewed_employer_name(
        prev_name, row.get("donor_key", ""), row.get("two_year_transaction_period", ""),
        context.scoped_employer_rules if row.get("identity_status") == "confirmed" else None,
    )
    return _result("retired", previous=prev_name)


def _resolve_row(row: pd.Series, context: ResolveContext, index=None) -> dict:
    """Resolve one row."""
    entity = row.get("entity_type", "")
    if entity == "COMMITTEE/PAC":
        return _result("committee")
    if entity == "ORGANIZATION":
        return _result("organization")

    employer = _s(row.get("contributor_employer")).strip()
    status = classify_employer_status(
        employer,
        row.get("contributor_occupation"),
        row.get("occupation_category"),
    )
    if status == "retired":
        return _resolve_retired(row, context, index)
    return _result(status)
