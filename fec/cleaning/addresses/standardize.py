"""One standard form for a filed US address, with no table of our own.

The usaddress parser names each word of a street line; the USPS
Publication 28 tables shipped with usaddress-scourgify give the short form
of street types, directions and unit designators; the USPS ZIP table
shipped with zipcodes names the cities of a ZIP. The same filing always
gives the same answer, offline.

A line the parser cannot name stays as filed, without its punctuation.
Foreign addresses are left as filed. Reviewed corrections in
data/rules/address_rules.csv are applied afterwards and win.
"""

from __future__ import annotations

import re
from functools import cache

import numpy as np
import pandas as pd
import usaddress
import zipcodes
from rapidfuzz.distance import OSA
from scourgify.address_constants import (
    CITY_ABBREVIATIONS,
    DIRECTIONAL_REPLACEMENTS,
    LONGHAND_STREET_TYPES,
    OCCUPANCY_TYPE_ABBREVIATIONS,
    STREET_TYPE_ABBREVIATIONS,
)

from fec.cleaning.addresses.foreign import foreign_address_mask
from fec.config.tables import words

FIELDS = ("street_1", "street_2", "city", "state", "zip")
COLUMNS = tuple(f"contributor_{field}" for field in FIELDS)

_UNIT_TYPES = {**OCCUPANCY_TYPE_ABBREVIATIONS, "NUMBER": "#", "NUM": "#", "NO": "#", "FLR": "FL", "PMB": "PMB"}
_UNIT_TYPES |= {short: short for short in OCCUPANCY_TYPE_ABBREVIATIONS.values()}
_NUMBERS = dict(zip("ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN".split(), "1 2 3 4 5 6 7 8 9 10".split()))
_ORDINALS = dict(
    zip(
        "FIRST SECOND THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH".split(),
        "1ST 2ND 3RD 4TH 5TH 6TH 7TH 8TH 9TH 10TH".split(),
    )
)
# USPS Publication 28: these designators stand without an id
_NEEDS_NO_ID = words("needs_no_id")
_PO_BOX = re.compile(r"^(?:P ?O|POST OFFICE|P ?O ?B)(?: ?BOX)? ?#? ?(\w*\d\w*|[A-Z])$")
_CARE_OF = re.compile(r"^(?:C/O|CARE OF|ATTN|ATTENTION)\b")


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,;]", " ", text)).strip()


def _ordinal(number: str) -> str:
    if not number.isdigit():
        return _ORDINALS.get(number, number)
    return number + (
        "TH" if number[-2:] in ("11", "12", "13") else {"1": "ST", "2": "ND", "3": "RD"}.get(number[-1], "TH")
    )


def _street(tagged: list) -> str | None:
    """Build the street from the parser's words: number, direction, name and type in USPS form."""
    labels = [label for _, label in tagged]
    words = [word for word, _ in tagged]
    if any(not label.startswith(("AddressNumber", "StreetName")) for label in labels):
        return None
    if (
        "StreetNamePostType" not in labels
        and labels[-2:] == ["StreetName", "StreetName"]
        and words[-1] in STREET_TYPE_ABBREVIATIONS
    ):
        labels[-1] = "StreetNamePostType"  # CENTRAL EXPRESSWAY
    # 750 SOUTH ST: a direction alone before the type is the street's name
    names_it = len(words) - labels.count("AddressNumber") == 2 and words[-1] in STREET_TYPE_ABBREVIATIONS
    for position, label in enumerate(labels):
        last_of_run = position + 1 == len(labels) or labels[position + 1] != label
        if label == "AddressNumber":
            words[position] = _NUMBERS.get(words[position], words[position])
        elif label.endswith("Directional") and "StreetName" in labels and not names_it:
            words[position] = DIRECTIONAL_REPLACEMENTS.get(words[position], words[position])
        elif label.endswith("Type") and last_of_run:
            words[position] = STREET_TYPE_ABBREVIATIONS.get(words[position], words[position])
        elif (
            label == "StreetName"
            and labels[position + 1 : position + 2] == ["StreetNamePostType"]
            and "StreetNamePreType" not in labels
        ):
            if position and labels[position - 1] != "StreetName":
                words[position] = _ordinal(words[position])  # 9 W 57 ST
    return " ".join(word for position, word in enumerate(words) if not position or word != words[position - 1])


def _unit(text: str) -> str:
    """Designator then id: STE 205, APT 13K, FL 10, # 420."""
    parts, kind = [], ""
    for word in text.replace("#", " # ").split():
        number = re.fullmatch(r"(\d+)(?:ST|ND|RD|TH)", _ORDINALS.get(word, word))
        if word in _UNIT_TYPES:
            if _UNIT_TYPES[word] == "FL" and parts and parts[-1].startswith("# "):
                parts[-1] = "FL " + parts[-1][2:]  # 5TH FLOOR
            elif not (word == "#" and kind):
                kind = _UNIT_TYPES[word]  # PMB #333 is PMB 333
        elif len(word) == 1 and word.isalpha() and parts and not kind:
            parts[-1] += word  # APT 10 F is APT 10F
        elif word not in ("NA", "NONE"):
            parts.append(f"{kind or '#'} {number.group(1) if number else word}")
            kind = ""
    return " ".join(dict.fromkeys(parts)) or (kind if kind in _NEEDS_NO_ID else "")


@cache
def parse_line(line: str) -> tuple[str, str] | None:
    """(street, unit) of one filed line; None when the parser cannot name every word."""
    text = _plain(line)
    if "@" in text:
        return "", ""  # an email address is no street
    text = re.sub(r"^(?:C/O|CARE OF|ATTN|ATTENTION)\b\D*(?=\d+ \S+ \S)", "", text)  # C/O JFI 410 PARK AVENUE
    text = re.sub(r"^(\d+)(?!(?:ST|ND|RD|TH)\b)([A-Z]{3,})\b", r"\1 \2", text)  # 13764RIVOLI
    text = re.sub(r"^((?:UNIT|APT|STE|SUITE) [\w-]+) (\d+ [A-Z].*)$", r"\2 \1", text)  # the unit typed first
    text = (
        re.sub(r"(?<=\w) (?:C/O|ATTN)\b.*$", "", text) if text[:1].isdigit() else text
    )  # 100 POND ST C/O IRVING KEMPNER
    if not text:
        return "", ""
    if box := _PO_BOX.match(text):
        return f"PO BOX {box.group(1)}", ""
    text, _, after_hash = text.partition("#")
    tagged = [(word.strip(" ,.;"), label) for word, label in usaddress.parse(text.strip())]
    if tagged and tagged[0][0].isdigit() and all(label == "BuildingName" for _, label in tagged):
        # 3 BETHESDA METRO CENTER: a numbered building is its own street
        tagged = [(tagged[0][0], "AddressNumber"), *((word, "StreetName") for word, _ in tagged[1:])]
    for at, (word, label) in enumerate(tagged[1:], 1):
        if (
            word in ("NO", "SO")
            and tagged[at - 1][1] == "StreetNamePostType"
            and at == len(tagged) - 1
            and not after_hash
        ):
            tagged[at] = (word[0], "StreetNamePostDirectional")  # 40TH AVENUE NO: north, as Minneapolis writes it
    cut = next((i for i, (_, label) in enumerate(tagged) if label.startswith(("Occupancy", "Subaddress"))), len(tagged))
    # HIGHWAY 101: the number of a numbered road; RICHMOND D: a cut-off street type
    if cut and cut < len(tagged) and tagged[cut][1].endswith("Identifier"):
        word, before = tagged[cut][0], tagged[cut - 1]
        numbered = word.isdigit() and STREET_TYPE_ABBREVIATIONS.get(before[0]) in ("HWY", "RTE", "FWY", "EXPY")
        if numbered or (len(word) == 1 and word.isalpha() and before[1] == "StreetName"):
            tagged[cut] = (word, "StreetName" if not numbered else "StreetNameNumber")
            cut += 1
    street = _street([(w, "StreetName" if label == "StreetNameNumber" else label) for w, label in tagged[:cut]])
    unit = _unit(" ".join([*(word for word, _ in tagged[cut:]), "#" + after_hash if after_hash.strip() else ""]))
    numbered = any(label == "AddressNumber" for _, label in tagged[:cut])
    if (
        street is None
        or any(not label.startswith(("Occupancy", "Subaddress")) for _, label in tagged[cut:])
        or (street and unit and not numbered)
    ):
        # the parser cannot name the line: a unit written at its end is still a unit
        ending = re.fullmatch(r"(.*\w) (?:SUITE|STE|APT|UNIT) ([\w-]+)", text.strip())
        return (
            (ending.group(1), _unit(text.strip()[len(ending.group(1)) :]))
            if ending and not after_hash.strip()
            else None
        )
    return (street, unit) if street or unit else None


def _initials(text: str) -> str:
    return "".join(word[0] for word in text.split())


def _repeats_the_place(text: str, city: str, state: str, zipcode: str) -> bool:
    """BALT MD, NEW, # LA, ANTA GA3034, FL USA: the city, state and ZIP again, often cut by the field."""
    words = [w for w in re.sub(r"[#\d]", " ", text).split() if w not in ("USA", "US", state)]
    rest = " ".join(words)
    if not rest:
        return bool(re.sub(r"[#\d ]", "", text))
    return len(rest) >= 2 and (
        city.startswith(rest) or _initials(city) == rest or (len(rest) >= 4 and city.endswith(rest))
    )


def standard_street(
    street_1: str, street_2: str, city: str = "", state: str = "", zipcode: str = ""
) -> tuple[str, str]:
    """Return the two street lines in standard form: the street, then the unit."""
    if street_2 and city and _repeats_the_place(_plain(street_2), city, state, zipcode):
        street_2 = ""
    # 4400 WEST 87TH TERRACE, 4400 WEST: the street typed twice
    first_part, *later = [_plain(part) for part in street_1.split(",")]
    start = first_part.split()[:2]
    words = " ".join(
        [
            first_part,
            *(
                part
                for part in later
                if not (
                    len(part) >= 4
                    and (
                        first_part.startswith(part) or (start[:1] and start[0].isdigit() and part.split()[:2] == start)
                    )
                )
            ),
        ]
    ).split()
    # 22649 SHAKER BLVD BEACHWOOD OH 441: the place typed after the street
    place = f"{city} {state} {zipcode}"
    for at in range(3, len(words)) if len(city) >= 4 else ():
        tail = " ".join(words[at:])
        if (
            len(tail) >= 4
            and (place.startswith(tail) or f"{city} {zipcode}".startswith(tail))
            and (len(tail) >= len(city) or words[at - 1] in STREET_TYPE_ABBREVIATIONS)
        ):
            words = words[:at]
            break
    street_1 = re.sub(r"^(?:BOX|POB|P O B) (?=\d+$)", "PO BOX ", " ".join(words))  # BOX 137 alone on the first line
    first, second = parse_line(street_1), parse_line(street_2)
    if first is None and second and second[0] and _CARE_OF.match(street_1):
        return second  # C/O a company, then the street
    one = first if first is not None else (street_1, "")
    # a second line the parser cannot name: NUM 303 is a unit, a designator alone is nothing, the rest stays as filed
    alone = _plain(street_2).replace("#", " # ").split()
    two = (
        second
        if second is not None
        else ("", _unit(street_2) if len(alone) <= 2 and alone[0] in _UNIT_TYPES else _plain(street_2))
    )
    if one[0] and two[0] and two[0] != one[0]:
        return one[0], " ".join(part for part in (one[1], *two) if part)  # a second street line
    return one[0] or two[0], " ".join(part for part in dict.fromkeys((one[1], two[1])) if part)


@cache
def _zip_cities(zipcode: str) -> tuple[str, ...]:
    if not (len(zipcode) == 5 and zipcode.isdigit()):
        return ()
    names = [
        name for record in zipcodes.matching(zipcode) for name in (record["city"], *record.get("acceptable_cities", []))
    ]
    return tuple(dict.fromkeys(name.upper() for name in names))


def _spelled_out(city: str) -> str:
    """ST LOUIS and FT WORTH in full; a last word with no vowel is a USPS short form (MOUNTAIN BRK, MAYFIELD HTS)."""
    words = [CITY_ABBREVIATIONS.get(word, word) for word in _plain(city).split()]
    if len(words) > 1 and not set(words[-1]) & set("AEIOUY"):
        words[-1] = LONGHAND_STREET_TYPES.get(words[-1], words[-1])
    return " ".join(words)


def _letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text)


def _shortens(short: str, full: str) -> bool:
    """HUNTINGTN BCH for HUNTINGTON BEACH: each word a shortening of the full word."""
    pairs = list(zip(short.split(), full.split()))
    if len(short.split()) != len(full.split()) or short == full:
        return False
    for word, whole in pairs:
        letters = iter(whole)
        if word[0] != whole[0] or not all(char in letters for char in word):
            return False
        if len(word) > 4 and set(whole) - set(word) - set("AEIOU"):
            return False  # NEWTON is no short form of NEWTONVILLE
    return True


def standard_city(city: str, zipcode: str) -> str:
    """Return the city in full, read through the USPS names of its ZIP: no short form, no slip of a letter."""
    full = _spelled_out(city)
    listed = [name for name in _zip_cities(zipcode[:5]) if set(name.split()[-1]) & set("AEIOUY")]
    listed = [_spelled_out(name) for name in listed]
    # USPS lists its own short forms too (PARADISE VLY, SLC): only the full names are answers
    names = list(
        dict.fromkeys(
            name for name in listed if not any(_shortens(name, other) or _initials(other) == name for other in listed)
        )
    )
    allowed = 2 if len(_letters(full)) >= 10 else 1
    tests = (
        lambda name: _letters(name) == _letters(full),
        lambda name: _shortens(full, name) or (" " in name and len(full) >= 2 and _initials(name) == full),
        lambda name: len(_letters(full)) >= 5 and OSA.distance(_letters(name), _letters(full)) <= allowed,
        lambda name: len(full) >= 5 and name.startswith(full + " "),  # SANTA for SANTA FE: cut short
    )
    for test in tests:
        if found := [name for name in names if test(name)]:
            return (
                max(found, key=len) if test is tests[1] else found[0] if len(found) == 1 or test is tests[0] else full
            )
    return full


def standard_address(street_1: str, street_2: str, city: str, state: str, zipcode: str) -> tuple[str, ...]:
    # 12230 BONE HOLLOW ROAD KAMAS 84036: a city and its own ZIP typed in the street are the real place
    *words, last = _plain(street_1).split() or [""]
    for size in (1, 2, 3):
        if len(words) >= size + 2 and " ".join(words[-size:]) in _zip_cities(last):
            street_1, city, zipcode, state = (
                " ".join(words[:-size]),
                " ".join(words[-size:]),
                last,
                zipcodes.matching(last)[0]["state"],
            )
            break
    # BETHESDA MARYLAND 20817, BOCA RATON FL: the state and ZIP typed after the city
    city = (
        re.sub(rf"(?:\s+{re.escape(state)})?(?:\s+\d{{5}}(?:-?\d{{4}})?)?$", "", _plain(city))
        if state
        else _plain(city)
    )
    digits = re.sub(r"\D", "", zipcode)
    five = digits.zfill(5)[:5] if len(digits) <= 5 else digits.zfill(9)[:5]
    town = standard_city(city, five)
    return (*standard_street(street_1, street_2, town, state, five), town, state, zipcode)


def standardize_addresses(df: pd.DataFrame) -> int:
    """Rewrite every US address in its standard form; return how many filings changed."""
    us = ~foreign_address_mask(df)
    if not us.any():
        return 0
    filed = df.loc[us, list(COLUMNS)].fillna("").astype(str).apply(lambda column: column.str.upper().str.strip())
    distinct = filed.drop_duplicates()
    standard = pd.DataFrame(
        [standard_address(*row) for row in distinct.itertuples(index=False, name=None)],
        columns=[f"{column}_standard" for column in COLUMNS],
        index=distinct.index,
    )
    merged = filed.merge(pd.concat([distinct, standard], axis=1), on=list(COLUMNS), how="left")
    merged.index = filed.index
    before = df.loc[us, list(COLUMNS)].fillna("").astype(str)
    for column in COLUMNS:
        df.loc[us, column] = merged[f"{column}_standard"].replace("", np.nan).astype(object)
    after = df.loc[us, list(COLUMNS)].fillna("").astype(str)
    return int((before != after).any(axis=1).sum())


def clear_own_name_street(df: pd.DataFrame) -> int:
    """Street_1 that only repeats the donor's own name is no street."""
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
