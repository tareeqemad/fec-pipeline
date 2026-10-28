"""A retiree's employer from another committee in the same cycle."""
import re

import pandas as pd

from fec.cleaning.work.previous import normalize_previous_employer_value
from fec.cleaning.addresses.compare import _ABBREVIATIONS
from fec.donor_match.normalize import NAME_SUFFIXES, NICKNAME_MAP, extract_generational_suffix
from fec.log import get_logger
from fec.resolve.cache import _s
from fec.resolve.previous import (
    _donor_evidence,
    _joint_name,
    _zip5,
)

logger = get_logger(__name__)
def _words(value) -> list[str]:
    """Return uppercase name/locality words without punctuation."""
    return re.findall(r"[A-Z0-9]+", _s(value).upper())

def _name_parts(value) -> tuple[str, str, str, str]:
    """Recognize known nicknames, keeping middle names and generation suffixes."""
    text = _s(value).strip()
    if "," in text:
        last, given = text.split(",", 1)
        last_words = _words(last)
        given_words = _words(given)
    else:
        words = [word for word in _words(text) if word not in NAME_SUFFIXES]
        last_words, given_words = words[-1:], words[:-1]

    last_words = [word for word in last_words if word not in NAME_SUFFIXES]
    given_words = [word for word in given_words if word not in NAME_SUFFIXES]
    last = " ".join(last_words)
    first = given_words[0] if given_words else ""
    middle = " ".join(given_words[1:])
    return last, NICKNAME_MAP.get(first, first), middle, extract_generational_suffix(text)

def _same_fec_donor(person: dict, record: dict) -> bool:
    """Match a compatible name and one complete filed address."""
    if _joint_name(record.get("contributor_name")) or _joint_name(person.get("name")):
        return False
    address = _filed_address(record)
    if not address or address[-1] in (person.get("shared_zips") or ()):
        return False
    filings = person.get("filings")
    if filings is None:
        filings = [{f"contributor_{field}": person.get(field, "") for field in
                    ("name", "street_1", "street_2", "state", "zip")}]
    return any(_filed_address(filing) == address
               and _same_name(filing.get("contributor_name"), record.get("contributor_name"))
               for filing in filings)

def _same_name(expected_name, reported_name) -> bool:
    if _joint_name(expected_name) or _joint_name(reported_name):
        return False
    expected, reported = _name_parts(expected_name), _name_parts(reported_name)
    if not expected[0] or len(expected[1]) < 2 or expected[:2] != reported[:2] or expected[3] != reported[3]:
        return False
    left, right = expected[2].split(), reported[2].split()
    return not left or not right or (len(left) == len(right) and all(
        a == b or (len(a) == 1 or len(b) == 1) and a[0] == b[0]
        for a, b in zip(left, right)))

def _filed_address(record: dict) -> tuple | None:
    street = _address_text(record.get("contributor_street_1"))
    unit = _address_text(record.get("contributor_street_2"))
    marker = re.search(r"\b(?:UNIT|STE|FL|RM|PH|BLDG|LOT|SPC|PMB)\b", street)
    if marker:
        unit = " ".join(filter(None, (street[marker.start():], unit)))
        street = street[:marker.start()].strip()
    street = " ".join(_ABBREVIATIONS.get(word.rstrip("."), word.rstrip(".")) for word in street.split())
    street = re.sub(r"\b([NS])\.? ?([EW])\.?(?= |$)", r"\1\2", street)
    state, zipcode = _s(record.get("contributor_state")).strip().upper(), _zip5(record.get("contributor_zip"))
    if not re.match(r"^\d+(?:[A-Z]|-\d+)?\s+\S", street) or len(state) != 2 or len(zipcode) != 5:
        return None
    return street, unit, state, zipcode

def _address_text(value) -> str:
    """Normalize unit labels without changing numbers or dropping text."""
    labels = {"APARTMENT": "UNIT", "APT": "UNIT", "UNIT": "UNIT", "SUITE": "STE",
              "STE": "STE", "FLOOR": "FL", "FL": "FL", "ROOM": "RM", "RM": "RM",
              "BUILDING": "BLDG", "BLDG": "BLDG"}
    text = _s(value).upper().replace(",", " ").replace("#", " UNIT ")
    text = re.sub(r"\b(" + "|".join(labels) + r")\.?(?=\s|\d|$)",
                  lambda match: labels[match.group(1)] + " ", text)
    return " ".join(text.split())

def _dated_source(person: dict, record: dict) -> bool:
    cycle = _s(person.get("cycle"))
    excluded, committee = set(person.get("committee_ids") or ()), _s(record.get("committee_id")).strip()
    if (not cycle.isdigit() or int(cycle) % 2 or _s(record.get("two_year_transaction_period")) != cycle
            or not excluded or not committee or committee in excluded):
        return False
    source = pd.to_datetime(record.get("contribution_receipt_date"), errors="coerce", utc=True)
    latest = pd.to_datetime(person.get("latest_date"), errors="coerce", utc=True)
    if not _s(record.get("sub_id")).strip() or pd.isna(source) or pd.isna(latest):
        return False
    start, end = pd.Timestamp(f"{int(cycle) - 1}-01-01", tz="UTC"), pd.Timestamp(f"{cycle}-12-31", tz="UTC")
    return start <= source.normalize() <= min(latest.normalize(), end)

def drop_unproven_source_entries(prev_cache, df: pd.DataFrame, cleared: dict | None = None) -> int:
    """Retain unverified candidates without using them as work history."""
    data = getattr(prev_cache, "data", prev_cache)
    candidates = {key: entry for key, entry in data.items()
                  if isinstance(entry, dict) and entry.get("method") in {"fec_api", "fec_api_unverified"}}
    if not candidates:
        return 0
    evidence = _donor_evidence(df)
    rejected, changed = [], 0
    for key, entry in candidates.items():
        parts = key.split(":")
        donor_key = parts[1] if len(parts) > 1 else key
        cycle = int(parts[2]) if len(parts) == 3 and parts[0] == "donor" and parts[2].isdigit() else 0
        if (str(entry.get("employer", "")).upper() in (cleared or {}).get(donor_key, ())
                or not normalize_previous_employer_value(entry.get("employer", ""))):
            rejected.append(key)
            continue
        person = {**evidence.get((donor_key, cycle), {}), "cycle": cycle}
        record = {f"contributor_{field}": entry.get(f"source_{field}", "") for field in
                  ("name", "street_1", "street_2", "zip")}
        record.update(contributor_state=entry.get("state", ""), sub_id=entry.get("source_sub_id", ""),
                      contribution_receipt_date=entry.get("source_date", ""),
                      two_year_transaction_period=entry.get("source_cycle", ""),
                      committee_id=entry.get("source_committee_id", ""))
        if not (_same_fec_donor(person, record) and _dated_source(person, record)):
            if entry.get("method") != "fec_api_unverified":
                entry["method"] = "fec_api_unverified"
                changed += 1
    for key in rejected:
        data.pop(key, None)
    return changed + len(rejected)
