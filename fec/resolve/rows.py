"""Choose an employer and office for one filing, respecting its date and identity."""

from dataclasses import dataclass, field

import pandas as pd

from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import reviewed_employer_name
from fec.cleaning.work.previous import normalize_previous_employer_value, preserve_own_named_legal_employer
from fec.cleaning.work.status import classify_employer_status
from fec.resolve.cache import _previous_for_row, _previous_employer_identity, _s
from fec.resolve.locations import select_location
from fec.resolve.previous import PROTECTED_METHODS


@dataclass(frozen=True)
class ResolveContext:
    previous_cache: object
    address_lookup: dict
    # row index -> the donor's own latest employer on or before that retired filing
    dated_previous: dict = field(default_factory=dict)
    # sub_id -> the employer the filing itself wrote (the raw file)
    filed_employers: dict = field(default_factory=dict)
    # (donor_key, employer) -> the first date the donor filed that employer
    first_filed: dict = field(default_factory=dict)
    previous_display_names: dict = field(default_factory=dict)
    scoped_employer_rules: dict = field(default_factory=dict)


def _result(
    status: str,
    entry: dict | None = None,
    *,
    method: str = "skip",
    confidence: str = "NONE",
    address: str = "",
    city: str = "",
    state: str = "",
    zip_code: str = "",
    previous: str = "",
    method_prefix: str = "",
) -> dict:
    """Return the complete, shared result shape."""
    source = entry or {}
    return {
        "employer_address": source.get("employer_address", address),
        "employer_city": source.get("employer_city", city),
        "employer_state": source.get("employer_state", state),
        "employer_zip": source.get("employer_zip", zip_code),
        "resolve_method": f"{method_prefix}{source.get('method', method)}",
        "resolve_confidence": source.get("confidence", confidence),
        "employer_status": status,
        "previous_employer": previous,
    }


def _own_address(row: pd.Series, state: str) -> dict | None:
    """Use a self-employed donor's own address when present."""
    street = _s(row.get("contributor_street_1"))
    if not street:
        return None
    return _result(
        "self_employed",
        method="self_employed_own_address",
        confidence="HIGH",
        address=street,
        city=_s(row.get("contributor_city")),
        state=state,
        zip_code=_s(row.get("contributor_zip")),
    )


def _cached_address(
    addr_cache,
    keys: tuple[str, ...],
    donor_zip: str = "",
    donor_state: str = "",
    donor_city: str = "",
) -> dict | None:
    """Return the first usable address under an exact or canonical cache key."""
    for key in keys:
        exact_key = str(key).upper()
        exact = addr_cache.get(exact_key)
        selected = select_location(exact, donor_zip, donor_state, donor_city)
        if selected:
            return selected
        if exact is not None:
            continue

        alias = addr_cache.get(canonical_key(exact_key))
        selected = select_location(alias, donor_zip, donor_state, donor_city)
        if selected:
            return selected
    return None


def _resolve_active(
    employer: str,
    state: str,
    zip_code: str,
    context: ResolveContext,
    city: str = "",
) -> dict:
    cached = _cached_address(
        context.address_lookup,
        (employer.upper(),),
        zip_code,
        state,
        city,
    )
    if not cached:
        return _result("active", method="pending")
    prefix = "" if cached.get("is_primary") else "nearest_"
    return _result("active", cached, method="ai_openai", method_prefix=prefix)


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
    """Check whether the cached employer was first learned after this filing."""
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
    """The clean stage's previous_employer when it is the filing's own employer."""
    text = str(row.get("previous_employer") or "").strip()
    previous = normalize_previous_employer_value(text)
    filed = normalize_previous_employer_value(context.filed_employers.get(str(row.get("sub_id", "")), ""))
    filed = reviewed_employer_name(
        filed, row.get("donor_key", ""), row.get("two_year_transaction_period", ""),
        context.scoped_employer_rules if row.get("identity_status") == "confirmed" else None,
    )
    if previous and filed and canonical_key(previous) == canonical_key(filed):
        # the text as set keeps an own-named firm's legal form: AMY N DEAN PA
        return {"employer": previous, "employer_source": text, "method": "filed"}
    return None


def _resolve_retired(
    row: pd.Series,
    context: ResolveContext,
    state: str,
    zip_code: str,
    index=None,
) -> dict:
    """Prefer reviewed history, then this filing, then dated history; keep unconfirmed filings separate."""
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
    prev_name, address_keys = _previous_employer_identity(prev_entry)
    if not prev_name:
        return _result("retired")

    prev_name = _display_previous_employer(
        prev_name,
        prev_entry,
        row.get("contributor_name", ""),
    )
    # Rename established history before choosing its company address.
    prev_name = context.previous_display_names.get(_s(row.get("sub_id")).strip(), prev_name)
    prev_name = reviewed_employer_name(
        prev_name, row.get("donor_key", ""), row.get("two_year_transaction_period", ""),
        context.scoped_employer_rules if row.get("identity_status") == "confirmed" else None,
    )
    if not prev_name:
        return _result("retired")
    legal_key = prev_name.strip().upper()
    address_keys = (legal_key, *(key for key in address_keys
                                if key != legal_key and canonical_key(key) == canonical_key(legal_key)))
    cached = _cached_address(
        context.address_lookup, address_keys, zip_code, state, _s(row.get("contributor_city"))
    )
    if not cached:
        return _result("retired", method="pending_address", previous=prev_name)

    location_prefix = "" if cached.get("is_primary") else "nearest_"
    method_prefix = f"{prev_entry.get('method', 'cross_record')}+{location_prefix}"
    return _result(
        "retired",
        cached,
        method="ai_openai",
        method_prefix=method_prefix,
        previous=prev_name,
    )


def _resolve_row(row: pd.Series, context: ResolveContext, index=None) -> dict:
    """Resolve one row."""
    entity = row.get("entity_type", "")
    state = _s(row.get("contributor_state")).strip()
    if entity == "COMMITTEE/PAC":
        return _result("committee")
    if entity == "ORGANIZATION":
        return _result("organization")

    employer = _s(row.get("contributor_employer")).strip()
    zip_code = _s(row.get("contributor_zip")).strip()
    status = classify_employer_status(
        employer,
        row.get("contributor_occupation"),
        row.get("occupation_category"),
    )
    if status == "active":
        return _resolve_active(employer, state, zip_code, context, _s(row.get("contributor_city")))
    if status == "retired":
        return _resolve_retired(row, context, state, zip_code, index)
    if status == "self_employed":
        return _own_address(row, state) or _result("self_employed")
    return _result(status)
