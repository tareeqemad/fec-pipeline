"""One standard form for a filed US address, with no table of our own.

The usaddress parser names each word of the street lines (house number,
direction, street name, street type, unit, PO box, recipient); the USPS
Publication 28 tables shipped with usaddress-scourgify abbreviate the
street type, the directions and the unit designator; the USPS ZIP table
shipped with zipcodes writes the city in full. The same filing always
gives the same answer, offline, so a monthly pull needs no review.

A line the parser cannot name stays as filed, without its punctuation.
Foreign addresses are left as filed. Reviewed corrections in
data/rules/address_rules.csv are applied afterwards and win.
"""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np
import pandas as pd
import usaddress
import zipcodes
from rapidfuzz.distance import OSA
from scourgify.address_constants import (
    CITY_ABBREVIATIONS,
    LONGHAND_STREET_TYPES,
    DIRECTIONAL_REPLACEMENTS,
    OCCUPANCY_TYPE_ABBREVIATIONS,
    STREET_TYPE_ABBREVIATIONS,
)

from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.config.tables import words

FIELDS = ("street_1", "street_2", "city", "state", "zip")
COLUMNS = tuple(f"contributor_{field}" for field in FIELDS)

_STREET = {"AddressNumberPrefix", "AddressNumber", "AddressNumberSuffix", "StreetNamePreDirectional", "StreetNamePreModifier", "StreetNamePreType", "StreetName", "StreetNamePostType", "StreetNamePostDirectional"}
_UNIT = {"OccupancyType", "OccupancyIdentifier", "SubaddressType", "SubaddressIdentifier"}
_BOX = {"USPSBoxType", "USPSBoxID"}
_DIRECTIONS = {**DIRECTIONAL_REPLACEMENTS, **{short: short for short in DIRECTIONAL_REPLACEMENTS.values()}}
_UNIT_TYPES = {
    **OCCUPANCY_TYPE_ABBREVIATIONS, **{short: short for short in OCCUPANCY_TYPE_ABBREVIATIONS.values()},
    "NUMBER": "#", "NUM": "#", "NO": "#", "FLR": "FL", "PMB": "PMB",
}
_NUMBER_WORDS = dict(zip("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN".split(), map(str, range(1, 11))))
_ORDINAL_WORDS = dict(zip(
    "FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH".split(),
    "1ST 2ND 3RD 4TH 5TH 6TH 7TH 8TH 9TH 10TH".split(),
))
_NUMBERED_ROADS = words("numbered_roads")
# USPS Publication 28: these designators stand without an id
_NEEDS_NO_ID = words("needs_no_id")
_CARE_OF = re.compile(r"^(?:C/O|C\\O|CARE OF|ATTN|ATTENTION)\b")
_PO_BOX = re.compile(r"^(?:P ?O|POST OFFICE)(?: BOX)?$")
_JOINED_UNIT = re.compile(r"^([A-Z]+)-(\w+)$")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,;]", " ", text)).strip()


def _ordinal(number: str) -> str:
    if not number.isdigit():
        return _ORDINAL_WORDS.get(number, number)
    tail = "TH" if number[-2:] in {"11", "12", "13"} else {"1": "ST", "2": "ND", "3": "RD"}.get(number[-1], "TH")
    return number + tail


# "5TH" FL is FL 5; "#1606" is 1606
def _unit_id(text: str) -> str:
    text = text.lstrip("#").strip()
    return re.sub(r"^(\d+)(?:ST|ND|RD|TH)$", r"\1", text)


def _unit(words: list[tuple[str, str]]) -> str:
    """Designator then id: STE 205, APT 13K, FL 10, # 420."""
    parts: list[str] = []
    kind = ""
    for position, (word, label) in enumerate(words):
        if label.endswith("Type"):
            if kind in _NEEDS_NO_ID:
                parts.append(kind)
            kind = _UNIT_TYPES.get(word.split()[-1], word)
            if position == len(words) - 1 and parts and parts[-1].startswith("# ") and kind != "#":
                # "5TH FLOOR" names the id before the designator
                parts[-1], kind = f"{kind} {parts[-1][2:]}", ""
            continue
        pieces = word.split()
        if kind and len(pieces) > 1 and all(len(piece) <= 4 for piece in pieces):
            # APT 10 F is APT 10F
            pieces = ["".join(pieces)]
        for piece in pieces:
            joined = _JOINED_UNIT.match(piece)
            if not kind and joined and joined.group(1) in _UNIT_TYPES:
                kind, piece = _UNIT_TYPES[joined.group(1)], joined.group(2)
            piece = _unit_id(piece)
            if piece in {"NA", "NONE"}:
                continue
            if piece:
                parts.append(f"{kind or '#'} {piece}")
            kind = ""
    if kind in _NEEDS_NO_ID:
        parts.append(kind)
    return " ".join(dict.fromkeys(parts))


def _street(words: list[tuple[str, str]]) -> str:
    labels = [label for _, label in words]
    # 750 SOUTH ST, 10 EAST CT: the direction is the street's name
    rest = " ".join(word for word, label in words if label != "AddressNumber").split()
    named = "StreetName" in labels and not (len(rest) == 2 and rest[1] in STREET_TYPE_ABBREVIATIONS)
    out = []
    for position, (word, label) in enumerate(words):
        following = labels[position + 1] if position + 1 < len(words) else ""
        if label == "AddressNumber":
            word = _NUMBER_WORDS.get(word, word)
        elif label.endswith("Directional") and named:
            word = _DIRECTIONS.get(word, word)
        elif label == "StreetNamePreType":
            # AVENUE V and AVE V are one street: one spelling for both
            word = " ".join(STREET_TYPE_ABBREVIATIONS.get(piece, piece) for piece in word.split())
        elif label == "StreetNamePostType":
            *name, kind = word.split()
            # WATER CLUB WAY keeps CLUB; BLVD LA is left for the place rule or a reviewed fix
            if not name or len(kind) > 2:
                word = " ".join([*name, STREET_TYPE_ABBREVIATIONS.get(kind, kind)])
            # 31 ST STREET
            if len(word.split()) == 2 and len({STREET_TYPE_ABBREVIATIONS.get(piece) for piece in word.split()}) == 1:
                word = word.split()[-1]
        elif label == "StreetName" and following == "StreetNamePostType" and "StreetNamePreType" not in labels:
            if len(word.split()) == 1:
                word = _ordinal(word)
        elif label == "StreetName" and "StreetNamePostType" not in labels and len(word.split()) > 1:
            # CENTRAL EXPRESSWAY: the type the parser read as part of the name
            *name, kind = word.split()
            word = " ".join([*name, STREET_TYPE_ABBREVIATIONS.get(kind, kind)])
        out.append(word)
    # 31 ST STREET: the type typed twice
    pieces = " ".join(out).split()
    return " ".join(piece for at, piece in enumerate(pieces) if at == 0 or piece != pieces[at - 1])


def _named(text: str) -> list[tuple[str, str]]:
    """The parser's (words, label) runs; everything after a # is the unit's id."""
    merged: list[tuple[str, str]] = []
    after_hash = False
    for word, label in usaddress.parse(text):
        word = word.strip(" ,.;")
        if word == "#":
            if not (merged and merged[-1][1] in {"OccupancyType", "SubaddressType"}) or after_hash:
                merged.append(("#", "OccupancyType"))
            after_hash = True
            continue
        if after_hash:
            label = "OccupancyIdentifier"
        if merged and merged[-1][1] == label and label != "AddressNumber":
            merged[-1] = (f"{merged[-1][0]} {word}", label)
        else:
            merged.append((word, label))
    return merged


@lru_cache(maxsize=None)
def parse_line(line: str) -> tuple[str, str] | None:
    """(street, unit) of one filed street line; None when the parser cannot name it."""
    text = re.sub(r"\s+", " ", re.sub(r"[.,;]", " ", line.replace("#", " # "))).strip()
    # NUM 1055 and NO 12 are # 1055 and # 12; 13764RIVOLI is 13764 RIVOLI
    text = re.sub(r"\b(?:NUM|NO|NUMBER) (?=\d)", "# ", text)
    text = re.sub(r"^(\d+)(?!(?:ST|ND|RD|TH)\b)([A-Z]{3,})\b", r"\1 \2", text)
    # UNIT 8606 7485 VICTORY LANE: the unit typed before the street
    text = re.sub(r"^((?:UNIT|APT|APARTMENT|STE|SUITE) [\w-]+|# \w+) (\d+ [A-Z].*)$", r"\2 \1", text)
    if not text or "@" in text:
        # an email address is no street
        return "", ""
    merged = _named(text)
    street: list[tuple[str, str]] = []
    unit: list[tuple[str, str]] = []
    box = ""
    for position, (word, label) in enumerate(merged):
        if label in {"Recipient", "BuildingName"} and _CARE_OF.match(word):
            continue
        if label == "BuildingName" and street and street[-1][1] == "StreetNamePostType":
            # 1777 REISTERSTOWN RD COMMERCE CE: the building's name after a complete street
            continue
        last = street[-1] if street else ("", "")
        if label == "BuildingName" and position == 0 and word.split()[0].isdigit() and len(word.split()) > 2:
            # 3 BETHESDA METRO CENTER: a numbered building is its own street
            number, *name = word.split()
            street += [(number, "AddressNumber"), (" ".join(name), "StreetName")]
        elif label in _STREET:
            # UNIT 8606 7485 VICTORY LANE: the unit may come first, never in the middle
            if box or (unit and street):
                return None
            street.append((word, label))
        elif label in _UNIT:
            first, *rest = word.split()
            road = STREET_TYPE_ABBREVIATIONS.get(last[0].split()[-1], "") if last[0] else ""
            numbered_road = last[1] == "StreetNamePostType" and road in _NUMBERED_ROADS and first.isdigit()
            # 24 RICHMOND D: a cut-off street type; MARKET PL CONNECTOR: a word of the name
            of_the_name = first.isalpha() and (
                (len(first) == 1 and last[1] == "StreetName") or (len(first) > 3 and first not in _UNIT_TYPES))
            if label == "OccupancyIdentifier" and not unit and last[1] == "StreetNamePostType" and first in {"NO", "SO"}:
                # 40TH AVENUE NO: north, the way Minneapolis writes it
                street.append((first[0], "StreetNamePostDirectional"))
                if rest:
                    unit.append((" ".join(rest), label))
                continue
            if not unit and street and (numbered_road or of_the_name) and (label == "OccupancyIdentifier" or of_the_name):
                street.append((first, "StreetNameTail"))
                if rest:
                    unit.append((" ".join(rest), label))
                continue
            if box == "PO" and first.isdigit():
                box = f"PO BOX {first}"
                continue
            unit.append((word, label))
        elif label == "USPSBoxType" and _PO_BOX.match(word):
            box = "PO BOX" if "BOX" in word else "PO"
        elif label == "USPSBoxID" and box in {"PO BOX", "PO"}:
            box = f"PO BOX {word.lstrip('# ').strip()}"
        elif label in {"PlaceName", "StateName"} and last[1] == "StreetNamePostType" and not unit and (
                len(merged) - position >= 2 and all(later in {"PlaceName", "StateName"} for _, later in merged[position:])):
            # 141 NOTTINGHILL RD BOSTON MASS, 1777 REISTERSTOWN RD COMMERCE CE: a place or building after the street
            break
        else:
            return None
    if box:
        return None if street or box in {"PO BOX", "PO"} else (box, _unit(unit))
    if not street and not unit:
        # C/O MOELIS: a line that is only a recipient stays as filed
        return None
    if street and "AddressNumber" not in [label for _, label in street]:
        # FAIRWAY DRIVE is a street; a line with no house number and a unit is not read
        return (_street(street), "") if "StreetName" in [label for _, label in street] and not unit else None
    return _street(street), _unit(unit)


def _initials(text: str) -> str:
    return "".join(word[0] for word in text.split())


# street_2 that only repeats the place: BALT MD, NEW, # LA, # CA, ANTA GA3034, FL USA
def _is_place_tail(text: str, city: str, state: str) -> bool:
    text = re.sub(rf"\b{re.escape(state)} ?\d+$", state, text.replace("#", " ")) if state else text.replace("#", " ")
    words = [word for word in text.split() if word not in {"USA", "US"}]
    named = len(words) < len(text.split())
    if words and words[-1] == state:
        words, named = words[:-1], True
    rest = " ".join(words)
    if not rest:
        return named
    return len(rest) >= 2 and not rest.isdigit() and (
        city.startswith(rest) or _initials(city) == rest or (len(rest) >= 4 and city.endswith(rest)))


# the city, state and ZIP typed again after the street, often cut by the 34-character field
def _without_place(street: str, city: str, state: str, zipcode: str) -> str:
    words = street.split()
    place = f"{city} {state} {zipcode}"
    for start in range(3, len(words)):
        tail = " ".join(words[start:])
        if len(tail) >= 4 and (place.startswith(tail) or f"{city} {zipcode}".startswith(tail)):
            # a cut-off city only after a complete street: BLVD SHERMAN O, not 955 S SPRING
            if len(tail) >= len(city) or words[start - 1] in STREET_TYPE_ABBREVIATIONS:
                return " ".join(words[:start])
    return street


def standard_street(street_1: str, street_2: str, city: str = "", state: str = "", zipcode: str = "") -> tuple[str, str]:
    """The two street lines in standard form: the street, then the unit."""
    if street_2 and city and _is_place_tail(_plain(street_2), city, state):
        street_2 = ""
    # 4400 WEST 87TH TERRACE, 4400 WEST: the street typed twice
    first_part, *later = [_plain(part) for part in street_1.split(",")]
    start = first_part.split()[:2]
    street_1 = " ".join([first_part, *(part for part in later if not (
        len(part) >= 4 and (first_part.startswith(part) or (start[0].isdigit() and part.split()[:2] == start))))])
    if len(city) >= 4:
        street_1 = _without_place(_plain(street_1), city, state, zipcode)
    # BOX 137 and POB 227 alone on the first line are PO boxes
    street_1 = re.sub(r"^(?:BOX|POB|P O B) (?=\d+$)", "PO BOX ", _plain(street_1))
    first, second = parse_line(street_1), parse_line(street_2)
    if first is None:
        if second and second[0] and _CARE_OF.match(_plain(street_1)):
            # C/O a company on the first line, the street on the second
            return second
        return _plain(street_1), (_plain(street_2) if second is None or second[0] else second[1])
    if second is None:
        # a second line the parser cannot name stays as filed
        return first[0], " ".join(part for part in (first[1], _plain(street_2)) if part)
    if first[0] and second[0] and second[0] != first[0]:
        return first[0], " ".join(part for part in (first[1], *second) if part)
    units = dict.fromkeys(part for part in (first[1], second[1]) if part)
    return first[0] or second[0], " ".join(units)


def _letters(text: str) -> str:
    return "".join(char for char in text if char.isalpha())


# ST LOUIS, FT WORTH
def _spelled_out(city: str) -> str:
    return " ".join(CITY_ABBREVIATIONS.get(word, word) for word in _plain(city).split())


@lru_cache(maxsize=None)
def _zip_cities(zipcode: str) -> tuple[str, ...]:
    if not (len(zipcode) == 5 and zipcode.isdigit()):
        return ()
    names: list[str] = []
    for record in zipcodes.matching(zipcode):
        names += [record["city"], *record.get("acceptable_cities", [])]
    return tuple(dict.fromkeys(name.upper() for name in names))


# HUNTINGTN BCH for HUNTINGTON BEACH: each word a shortening of the full word
def _shortens(short: str, full: str) -> bool:
    words, full_words = short.split(), full.split()
    if len(words) != len(full_words) or short == full:
        return False
    for word, whole in zip(words, full_words):
        letters = iter(whole)
        if word[0] != whole[0] or not all(char in letters for char in word):
            return False
        # NEWTON is no short form of NEWTONVILLE: a long word may only drop vowels
        if len(word) > 4 and any(char not in "AEIOU" for char in set(whole) - set(word)):
            return False
    return True


def standard_city(city: str, zipcode: str) -> str:
    """The city in full, read through the USPS names of its ZIP: no abbreviation, no slip of a letter."""
    city = _plain(city)
    names = _zip_cities(zipcode[:5])
    if not city:
        return city
    full = _spelled_out(city)
    target = _letters(full)
    whole = [_spelled_out(name) for name in names]
    # a last word in its USPS short form: MOUNTAIN BRK, MAYFIELD HTS
    *head, last = full.split()
    suffixed = " ".join([*head, LONGHAND_STREET_TYPES.get(last, last)])
    longer = [name for name in whole if name != city and (name in {full, suffixed} or _shortens(full, name) or (
        " " in name and len(city) >= 2 and _initials(name) == city))]
    if longer:
        return max(longer, key=len)
    if head and not set(last) & set("AEIOUY"):
        # BRK, HTS, BCH: a last word with no vowel is the USPS short form
        return suffixed
    if city in names and _letters(city) != _letters(names[0]):
        return city
    for name in names:
        if _letters(_spelled_out(name)) == target:
            return name
    allowed = 2 if len(target) >= 10 else 1
    close = [name for name in names if len(target) >= 5 and OSA.distance(_letters(name), target) <= allowed]
    return close[0] if len(close) == 1 else full


def standard_address(street_1: str, street_2: str, city: str, state: str, zipcode: str) -> tuple[str, ...]:
    # 12230 BONE HOLLOW ROAD KAMAS 84036: a city and its own ZIP typed in the street are the real place
    *words, last = _plain(street_1).split() or [""]
    for size in (1, 2, 3):
        typed = " ".join(words[-size:])
        if len(words) >= size + 2 and typed in _zip_cities(last):
            street_1, city, zipcode = " ".join(words[:-size]), typed, last
            state = zipcodes.matching(last)[0]["state"]
            break
    # BETHESDA MARYLAND 20817, BOCA RATON FL: the state and ZIP typed after the city
    city = re.sub(rf"(?:\s+{re.escape(state)})?(?:\s+\d{{5}}(?:-?\d{{4}})?)?$", "", _plain(city)) if state else city
    five = re.sub(r"\D", "", zipcode).zfill(5)[:5] if len(re.sub(r"\D", "", zipcode)) <= 5 else re.sub(r"\D", "", zipcode).zfill(9)[:5]
    town = standard_city(city, five)
    street, unit = standard_street(street_1, street_2, town, state, five)
    return street, unit, town, state, zipcode


def standardize_addresses(df: pd.DataFrame) -> int:
    """Rewrite every US address in its standard form; return how many filings changed."""
    us = ~foreign_address_mask(df)
    if not us.any():
        return 0
    filed = df.loc[us, list(COLUMNS)].fillna("").astype(str).apply(lambda column: column.str.upper().str.strip())
    distinct = filed.drop_duplicates()
    standard = pd.DataFrame(
        [standard_address(*row) for row in distinct.itertuples(index=False, name=None)],
        columns=[f"{column}_standard" for column in COLUMNS], index=distinct.index,
    )
    merged = filed.merge(pd.concat([distinct, standard], axis=1), on=list(COLUMNS), how="left")
    merged.index = filed.index
    before = df.loc[us, list(COLUMNS)].fillna("").astype(str)
    for column in COLUMNS:
        df.loc[us, column] = merged[f"{column}_standard"].replace("", np.nan).astype(object)
    after = df.loc[us, list(COLUMNS)].fillna("").astype(str)
    return int((before != after).any(axis=1).sum())


# street_1 that only repeats the donor's own name is no street
def clear_own_name_street(df: pd.DataFrame) -> int:
    street = df["contributor_street_1"].fillna("").astype(str).str.upper()
    first = df["contributor_first_name"].fillna("").astype(str).str.strip().str.upper()
    last = df["contributor_last_name"].fillna("").astype(str).str.strip().str.upper()
    is_name = (
        ((street == first) & (first.str.len() >= 3))
        | ((street == last) & (last.str.len() >= 3))
        | (street == first + " " + last)
        | (street == last + " " + first)
    )
    if is_name.any():
        df.loc[is_name, "contributor_street_1"] = np.nan
    return int(is_name.sum())
