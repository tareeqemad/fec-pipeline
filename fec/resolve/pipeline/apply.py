"""Apply resolved addresses to DataFrame columns."""

import pandas as pd

from fec.cleaning.employer_synonyms import (
    normalize_employer_display_name,
    EMPLOYER_SYNONYMS,
)
from .constants import RETIRED, SELF_EMPLOYED
from fec.cleaning.previous_employer import is_real_employer
from .helpers import _s, _prev_key
from .quality_fixes import (
    _clear_nonindividual_employer,
    _fix_employer_address_quality,
)


def apply_results(df: pd.DataFrame, prev_cache, addr_cache, comm_cache) -> pd.DataFrame:
    """Write resolved addresses to DataFrame columns."""
    cols = {
        "employer_address": [], "employer_city": [],
        "employer_state": [], "employer_zip": [],
        "resolve_method": [], "resolve_confidence": [],
        "employer_status": [], "previous_employer": [],
    }

    for _, row in df.iterrows():
        result = _resolve_row(row, prev_cache, addr_cache, comm_cache)
        for column in cols:
            cols[column].append(result.get(column, ""))

    for column, values in cols.items():
        df[column] = values

    _fix_employer_address_quality(df)
    # A committee/org IS the entity - its address is already the donor address,
    # so don't duplicate it into employer_*.
    _clear_nonindividual_employer(df)

    return df


def _resolve_row(row: pd.Series, prev_cache, addr_cache, comm_cache) -> dict:
    """Resolve one row."""
    EMPTY = {
        "employer_address": "", "employer_city": "",
        "employer_state": "", "employer_zip": "",
        "resolve_method": "skip", "resolve_confidence": "NONE",
        "employer_status": "", "previous_employer": "",
    }

    entity = row.get("entity_type", "")
    emp = _s(row.get("contributor_employer")).strip()
    emp_upper = emp.upper()
    state = _s(row.get("contributor_state")).strip()

    if entity == "COMMITTEE/PAC":
        name = str(row.get("contributor_name", ""))
        key = f"{name}|{state}"
        cached = comm_cache.get(key)
        if cached and cached.get("employer_address"):
            return {
                "employer_address": cached["employer_address"],
                "employer_city": cached.get("employer_city", ""),
                "employer_state": cached.get("employer_state", state),
                "employer_zip": cached.get("employer_zip", ""),
                "resolve_method": cached.get("method", "fec_api"),
                "resolve_confidence": cached.get("confidence", "HIGH"),
                "employer_status": "committee",
                "previous_employer": "",
            }
        street = _s(row.get("contributor_street_1"))
        if street:
            return {
                "employer_address": street,
                "employer_city": _s(row.get("contributor_city")),
                "employer_state": state,
                "employer_zip": _s(row.get("contributor_zip")),
                "resolve_method": "committee_own_address",
                "resolve_confidence": "LOW",
                "employer_status": "committee",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "committee"
        return EMPTY

    # An org IS the entity (its address is the donor address) - no employer lookup.
    if entity == "ORGANIZATION":
        EMPTY["employer_status"] = "organization"
        return EMPTY

    # Real employer - cache is keyed by EMPLOYER (corporate HQ, no per-state).
    if is_real_employer(emp):
        key = emp_upper
        cached = addr_cache.get(key)
        # A manual_override counts as resolved on city/state alone (deliberate
        # human decision); AI and other entries still need a street.
        if cached and (cached.get("employer_address") or (
            cached.get("method") == "manual_override" and cached.get("employer_city")
        )):
            return {
                "employer_address": cached.get("employer_address", ""),
                "employer_city": cached.get("employer_city", ""),
                "employer_state": cached.get("employer_state", ""),
                "employer_zip": cached.get("employer_zip", ""),
                "resolve_method": cached.get("method", "ai_openai"),
                "resolve_confidence": cached.get("confidence", "HIGH"),
                "employer_status": "active",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "active"
        EMPTY["resolve_method"] = "pending"
        return EMPTY

    if emp_upper == RETIRED:
        prev_key = _prev_key(row.get("contributor_name", ""), state)
        prev_entry = prev_cache.get(prev_key)
        if prev_entry and prev_entry.get("employer"):
            prev_name_raw = _s(prev_entry["employer"]).strip()
            # The cache was never cleaned - run the same normalization
            # contributor_employer gets, or "DuPont" etc. leaks through raw.
            prev_name = normalize_employer_display_name(prev_name_raw) or ""
            prev_normalized = _s(prev_entry.get("employer_normalized", prev_name_raw)).strip().upper()
            # EMPLOYER_SYNONYMS keeps previous_employer lookups on the same
            # cache keys as contributor_employer (e.g. WHATSAPP -> WHATSAPP LLC).
            prev_normalized = EMPLOYER_SYNONYMS.get(prev_normalized, prev_normalized)
            prev_name = EMPLOYER_SYNONYMS.get(prev_name.upper(), prev_name) or prev_name
            key = prev_normalized
            cached = addr_cache.get(key)
            if cached and cached.get("employer_address"):
                return {
                    "employer_address": cached["employer_address"],
                    "employer_city": cached.get("employer_city", ""),
                    "employer_state": cached.get("employer_state", ""),
                    "employer_zip": cached.get("employer_zip", ""),
                    "resolve_method": f"{prev_entry.get('method','cross_record')}+{cached.get('method','ai_openai')}",
                    "resolve_confidence": cached.get("confidence", "HIGH"),
                    "employer_status": "retired",
                    "previous_employer": prev_name,
                }
            return {
                "employer_address": "", "employer_city": "",
                "employer_state": "", "employer_zip": "",
                "resolve_method": "pending_address",
                "resolve_confidence": "NONE",
                "employer_status": "retired",
                "previous_employer": prev_name,
            }
        EMPTY["employer_status"] = "retired"
        return EMPTY

    if emp_upper == SELF_EMPLOYED:
        street = _s(row.get("contributor_street_1"))
        if street:
            return {
                "employer_address": street,
                "employer_city": _s(row.get("contributor_city")),
                "employer_state": state,
                "employer_zip": _s(row.get("contributor_zip")),
                "resolve_method": "self_employed_own_address",
                "resolve_confidence": "HIGH",
                "employer_status": "self_employed",
                "previous_employer": "",
            }
        EMPTY["employer_status"] = "self_employed"
        return EMPTY

    EMPTY["employer_status"] = "not_employed" if emp_upper in (
        "NOT EMPLOYED", "STUDENT", "HOMEMAKER", "UNEMPLOYED"
    ) else "missing"
    return EMPTY
