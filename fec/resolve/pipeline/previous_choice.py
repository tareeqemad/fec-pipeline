"""Choose and display a filing's previous employer."""

import pandas as pd

from fec.cleaning.employer_synonyms.apply import expand_abbreviation_text
from fec.cleaning.employer_synonyms.canonical import canonical_key
from fec.cleaning.previous_employer import (
    normalize_previous_employer_value,
    preserve_own_named_legal_employer,
)
from fec.io import cell_text
from fec.resolve.pipeline.previous_cache import PROTECTED_METHODS, previous_cache_key


# each donor's first filing date per employer
def first_filed_employers(df: pd.DataFrame) -> dict:
    dates = pd.to_datetime(df["contribution_receipt_date"], errors="coerce")
    working = df["entity_type"].eq("INDIVIDUAL") & dates.notna()
    employers = df.loc[working, "contributor_employer"].fillna("")
    frame = pd.DataFrame({"donor": df.loc[working, "donor_key"], "employer": employers, "date": dates[working]})
    frame = frame[frame["employer"].ne("")]
    return frame.groupby(["donor", "employer"])["date"].min().to_dict()


# true if the entry postdates this filing
def learned_later(entry: dict, row: pd.Series, first_filed: dict) -> bool:
    filed = pd.to_datetime(row["contribution_receipt_date"], errors="coerce")
    if pd.isna(filed):
        return False
    source = pd.to_datetime(entry.get("source_date"), errors="coerce")
    if pd.notna(source):
        return source > filed
    donor = row["donor_key"]
    names = {str(entry.get(key) or "").strip().upper() for key in ("employer", "employer_source")} - {""}
    firsts = [first_filed[(donor, name)] for name in names if (donor, name) in first_filed]
    return bool(firsts) and min(firsts) > filed


# the previous employer the filing wrote itself
def filed_previous_entry(row: pd.Series, filed_employers: dict) -> dict | None:
    text = cell_text(row["previous_employer"])
    previous = normalize_previous_employer_value(text)
    filed = normalize_previous_employer_value(filed_employers.get(str(row["sub_id"]), ""))
    if previous and filed and canonical_key(previous) == canonical_key(filed):
        # keeps an own-named firm's legal form
        return {"employer": previous, "employer_source": text, "method": "filed"}
    return None


# pick this filing's previous employer entry
def choose_previous_entry(row: pd.Series, context, index) -> dict | None:
    own_entry = filed_previous_entry(row, context.filed_employers)
    if row["identity_status"] != "confirmed":
        return own_entry
    prev_entry = context.previous_cache.get(previous_cache_key(row["donor_key"]))
    if prev_entry and prev_entry.get("method") in PROTECTED_METHODS:
        return prev_entry
    if own_entry:
        return own_entry
    dated = context.dated_previous.get(index)
    if dated:
        return {"employer": dated, "method": "cross_record"}
    if prev_entry and learned_later(prev_entry, row, context.first_filed):
        return None
    return prev_entry


# the own-named legal form from cache sources
def display_previous_employer(prev_name: str, prev_entry: dict, donor_name: str) -> str:
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


# keep the clean-stage spelling per company
def preserve_previous_employer_display(df: pd.DataFrame, prior_previous: pd.Series) -> None:
    prior = prior_previous.fillna("")
    current = df["previous_employer"].fillna("").astype(str).str.strip()
    prior_key = prior.map(canonical_key)
    same_company = prior_key.ne("") & current.map(canonical_key).eq(prior_key)
    if same_company.any():
        df.loc[same_company, "previous_employer"] = prior[same_company]


# expand abbreviations as clean.py does
def expand_previous_employer_abbreviations(df: pd.DataFrame) -> None:
    previous = df["previous_employer"].fillna("").astype(str)
    named = previous.str.strip().ne("")
    if named.any():
        df.loc[named, "previous_employer"] = expand_abbreviation_text(previous[named])
