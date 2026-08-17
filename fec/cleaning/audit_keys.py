"""Same key means same value, different spelling."""
from __future__ import annotations

import re

import pandas as pd

from fec.config.cities import expand_city_abbreviations
from fec.config.constants import RETIRED_TYPO_EMPLOYERS, SELF_EMPLOYED_TYPOS
from fec.config.data import COMM_TAIL_RE
from fec.config.employers import EMPLOYER_ABBREVIATIONS
from fec.config.geography import US_STATES
from fec.config.streets import (
    DIRECTION_ABBREVIATIONS,
    POBOX_RE,
    STREET_TYPE_ABBREVIATIONS,
)

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_SELF_EMPLOYED_RE = re.compile(r"^SELF([\s\-/]*EMP(LOY\w*)?)?$")

_STREET_TOKEN_MAP = dict(STREET_TYPE_ABBREVIATIONS) | dict(DIRECTION_ABBREVIATIONS)
_UNIT_DESIGNATORS = frozenset({
    "APT", "APARTMENT", "UNIT", "STE", "SUITE", "BLDG", "BUILDING",
    "DEPT", "DEPARTMENT", "OFF", "OFFICE", "FLOOR", "FL", "FLR",
    "RM", "ROOM", "PH", "PENTHOUSE", "NO", "NUMBER",
})
_LEGAL_SUFFIXES = frozenset({
    "INC", "INCORPORATED", "LLC", "LLP", "LP", "LTD", "LIMITED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "PLLC", "PC", "PA",
})
_PERSON_TOKENS = frozenset({
    "MR", "MRS", "MS", "DR", "REV", "HON", "PROF", "JR", "SR", "II", "III",
    "IV", "ESQ", "MD", "PHD", "DDS", "DO", "CPA", "FACS", "FAAOS",
})
_EMPLOYER_TOKEN_MAP = {
    token: expansion
    for token, expansion in EMPLOYER_ABBREVIATIONS.items()
    if expansion
} | {"ASSOCS": "ASSOC", "ASSOCIATES": "ASSOC", "ASSOCIATION": "ASSOC"}
_RETIRED_VARIANTS = frozenset({"RETIRE", "RETIRD", "RETIREE", "RETIRED"}) | RETIRED_TYPO_EMPLOYERS
_NOT_EMPLOYED_VARIANTS = frozenset({"NOT EMPLOYED", "UNEMPLOYED", "NOTEMPLOYED"})
_TRUE_FLAGS = frozenset({"t", "true", "1", "yes"})


def _tokens(value: str) -> list[str]:
    return [token for token in _NON_ALNUM_RE.split(value.upper()) if token]


def street_key(value: str) -> str:
    text = POBOX_RE.sub("PO BOX", value.upper())
    tokens = set()
    for token in _tokens(text):
        token = _STREET_TOKEN_MAP.get(token, token)
        if token in _UNIT_DESIGNATORS:
            continue
        if token.isdigit():
            token = token.lstrip("0") or "0"
        tokens.add(token)
    return " ".join(sorted(tokens))


def city_key(value: str) -> str:
    tokens = _tokens(expand_city_abbreviations(value.upper()))
    while tokens and (tokens[-1] in US_STATES or tokens[-1].isdigit()):
        tokens.pop()
    return " ".join(tokens)


def zip_key(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if not digits:
        return ""
    digits = digits[:5] if len(digits) > 5 else digits.zfill(5)
    return "" if digits == "00000" else digits


def person_name_key(value: str, ordered: bool = True) -> str:
    tokens = [token for token in _tokens(value) if token not in _PERSON_TOKENS]
    if not ordered:
        tokens = sorted(set(tokens))
    return " ".join(tokens)


def organization_name_key(value: str) -> str:
    text = COMM_TAIL_RE.sub("", value.upper())
    tokens = [token for token in _tokens(text) if token not in _LEGAL_SUFFIXES]
    return " ".join(tokens)


def _status_word(text: str) -> str | None:
    if _SELF_EMPLOYED_RE.match(text) or text in SELF_EMPLOYED_TYPOS:
        return "SELF-EMPLOYED"
    if text in _RETIRED_VARIANTS:
        return "RETIRED"
    if text in _NOT_EMPLOYED_VARIANTS:
        return "NOT EMPLOYED"
    return None


def employer_key(value: str) -> str:
    text = value.upper().strip()
    status = _status_word(text)
    if status:
        return status
    tokens = [
        _EMPLOYER_TOKEN_MAP.get(token, token)
        for token in _tokens(text.replace("&", " AND "))
        if token not in _LEGAL_SUFFIXES and token != "THE"
    ]
    return " ".join(tokens)


def occupation_key(value: str) -> str:
    text = value.upper().strip()
    return _status_word(text) or " ".join(_tokens(text.replace("&", " AND ")))


def flag_key(value: str) -> str:
    return "true" if value.strip().lower() in _TRUE_FLAGS else "false"


def identity_key(value: str) -> str:
    return value.upper().strip()


_KEY_BY_FIELD = {
    "contributor_street_1": street_key,
    "contributor_street_2": street_key,
    "contributor_city": city_key,
    "contributor_state": identity_key,
    "contributor_zip": zip_key,
    "contributor_first_name": person_name_key,
    "contributor_last_name": person_name_key,
    "contributor_employer": employer_key,
    "previous_employer": employer_key,
    "contributor_occupation": occupation_key,
    "is_individual": flag_key,
}


def format_key(field: str, values: pd.Series, individual: pd.Series | None = None) -> pd.Series:
    """Format key per value; names need flag."""
    if field == "contributor_name":
        person = values.map(lambda value: person_name_key(value, ordered=False))
        if individual is None:
            return person
        organization = values.map(organization_name_key)
        return person.where(individual.reindex(values.index).fillna(False).astype(bool), organization)
    key = _KEY_BY_FIELD.get(field, identity_key)
    return values.map(key)
