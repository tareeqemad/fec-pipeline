"""One standard form for a filed US address, offline and the same for the same filing.

usaddress names each word of a street line; the USPS Publication 28 tables of usaddress-scourgify give the short
forms; the USPS ZIP table of zipcodes names the cities of a ZIP. A line the parser cannot name stays as filed,
without its punctuation. Foreign addresses stay as filed; data/rules/address_rules.csv is applied afterwards.
"""

from __future__ import annotations

import re
from functools import cache

import numpy as np
import pandas as pd
import usaddress
from rapidfuzz.distance import OSA
from scourgify.address_constants import (
    CITY_ABBREVIATIONS,
    DIRECTIONAL_REPLACEMENTS,
    LONGHAND_STREET_TYPES,
    OCCUPANCY_TYPE_ABBREVIATIONS,
    STREET_TYPE_ABBREVIATIONS,
)

from fec.cleaning.addresses.donor_addresses import zip_place
from fec.cleaning.addresses.foreign_addresses import ADDRESS_FIELDS, foreign_address_mask
from fec.common.text import upper_text
from fec.vocabulary.tables import CONFIG_DIR, read_rows, words


def _read_address_words(kind: str) -> dict[str, str]:
    return {row["word"]: row["short"] for row in read_rows(CONFIG_DIR / "address_words.csv") if row["kind"] == kind}


_UNIT_TYPES = {**OCCUPANCY_TYPE_ABBREVIATIONS, **_read_address_words("unit")}
_UNIT_TYPES |= {short: short for short in OCCUPANCY_TYPE_ABBREVIATIONS.values()}
_NUMBERS = _read_address_words("number")
_ORDINALS = _read_address_words("ordinal")
# USPS Publication 28: these designators stand without an id
_NEEDS_NO_ID = words("needs_no_id")
# Words that start a street's name but that the parser reads as a street type before it: BEACH 137TH ST
_NAME_WORDS_READ_AS_TYPES = words("name_words_read_as_types")
_PO_BOX = re.compile(r"^(?:P ?O|POST OFFICE|P ?O ?B)(?: ?BOX)? ?#? ?(\w*\d\w*|[A-Z])$")
_CARE_OF = re.compile(r"^(?:C/O|CARE OF|ATTN|ATTENTION)\b")


def _strip_punctuation(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.,;]", " ", text)).strip()


def _to_ordinal(number: str) -> str:
    if not number.isdigit():
        return _ORDINALS.get(number, number)
    if number[-2:] in ("11", "12", "13"):
        return number + "TH"
    return number + {"1": "ST", "2": "ND", "3": "RD"}.get(number[-1], "TH")


def _build_street(tagged: list) -> str | None:
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
    direction_is_name = len(words) - labels.count("AddressNumber") == 2 and words[-1] in STREET_TYPE_ABBREVIATIONS
    for position, label in enumerate(labels):
        last_of_run = position + 1 == len(labels) or labels[position + 1] != label
        if label == "AddressNumber":
            words[position] = _NUMBERS.get(words[position], words[position])
        elif label.endswith("Directional") and "StreetName" in labels and not direction_is_name:
            words[position] = DIRECTIONAL_REPLACEMENTS.get(words[position], words[position])
        elif label.endswith("Type") and last_of_run:
            if not (label == "StreetNamePreType" and words[position] in _NAME_WORDS_READ_AS_TYPES):
                words[position] = STREET_TYPE_ABBREVIATIONS.get(words[position], words[position])
        elif (
            label == "StreetName"
            and labels[position + 1 : position + 2] == ["StreetNamePostType"]
            and "StreetNamePreType" not in labels
            and position
            and labels[position - 1] != "StreetName"
        ):
            words[position] = _to_ordinal(words[position])  # 9 W 57 ST
    return " ".join(word for position, word in enumerate(words) if not position or word != words[position - 1])


def _build_unit(text: str) -> str:
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


def _strip_line_extras(text: str) -> str:
    # C/O JFI 410 PARK AVENUE
    text = re.sub(r"^(?:C/O|CARE OF|ATTN|ATTENTION)\b\D*(?=\d+ \S+ \S)", "", text)
    # 13764RIVOLI
    text = re.sub(r"^(\d+)(?!(?:ST|ND|RD|TH)\b)([A-Z]{3,})\b", r"\1 \2", text)
    # the unit typed first
    text = re.sub(r"^((?:UNIT|APT|STE|SUITE) [\w-]+) (\d+ [A-Z].*)$", r"\2 \1", text)
    if text[:1].isdigit():
        # 100 POND ST C/O IRVING KEMPNER
        text = re.sub(r"(?<=\w) (?:C/O|ATTN)\b.*$", "", text)
    return text


def _tag_words(text: str, after_hash: str) -> list[tuple[str, str]]:
    tagged = [(word.strip(" ,.;"), label) for word, label in usaddress.parse(text.strip())]
    if tagged and tagged[0][0].isdigit() and all(label == "BuildingName" for _, label in tagged):
        # 3 BETHESDA METRO CENTER: a numbered building is its own street
        tagged = [(tagged[0][0], "AddressNumber"), *((word, "StreetName") for word, _ in tagged[1:])]
    if len(tagged) >= 2 and tagged[-1][0] in ("NO", "SO") and tagged[-2][1] == "StreetNamePostType" and not after_hash:
        # 40TH AVENUE NO: north, as Minneapolis writes it
        tagged[-1] = (tagged[-1][0][0], "StreetNamePostDirectional")
    return tagged


def _find_street_end(tagged: list[tuple[str, str]]) -> int:
    # Where the street words end; HIGHWAY 101 (a road number) and RICHMOND D (a cut-off type) stay street.
    unit_starts = [i for i, (_, label) in enumerate(tagged) if label.startswith(("Occupancy", "Subaddress"))]
    cut = unit_starts[0] if unit_starts else len(tagged)
    if not cut or cut == len(tagged) or not tagged[cut][1].endswith("Identifier"):
        return cut
    word, before = tagged[cut][0], tagged[cut - 1]
    road_type = STREET_TYPE_ABBREVIATIONS.get(before[0])
    numbered_road = word.isdigit() and road_type in ("HWY", "RTE", "FWY", "EXPY")
    cut_off_type = len(word) == 1 and word.isalpha() and before[1] == "StreetName"
    if not (numbered_road or cut_off_type):
        return cut
    tagged[cut] = (word, "StreetNameNumber" if numbered_road else "StreetName")
    return cut + 1


def _read_unit_at_end(text: str, after_hash: str) -> tuple[str, str] | None:
    line = text.strip()
    ending = re.fullmatch(r"(.*\w) (?:SUITE|STE|APT|UNIT) ([\w-]+)", line)
    if not ending or after_hash.strip():
        return None
    street = ending.group(1)
    return street, _build_unit(line[len(street) :])


@cache
def parse_line(line: str) -> tuple[str, str] | None:
    """(street, unit) of one filed line; None when the parser cannot name every word."""
    text = _strip_punctuation(line)
    if "@" in text:
        return "", ""  # an email address is no street
    text = _strip_line_extras(text)
    if not text:
        return "", ""
    if box := _PO_BOX.match(text):
        return f"PO BOX {box.group(1)}", ""
    text, _, after_hash = text.partition("#")
    tagged = _tag_words(text, after_hash)
    cut = _find_street_end(tagged)
    street_words = [(word, "StreetName" if label == "StreetNameNumber" else label) for word, label in tagged[:cut]]
    street = _build_street(street_words)
    unit_words = [word for word, _ in tagged[cut:]]
    hash_unit = "#" + after_hash if after_hash.strip() else ""
    unit = _build_unit(" ".join([*unit_words, hash_unit]))
    has_number = any(label == "AddressNumber" for _, label in tagged[:cut])
    if (
        street is None
        or any(not label.startswith(("Occupancy", "Subaddress")) for _, label in tagged[cut:])
        or (street and unit and not has_number)
    ):
        return _read_unit_at_end(text, after_hash)
    return (street, unit) if street or unit else None


def _get_initials(text: str) -> str:
    return "".join(word[0] for word in text.split())


def _repeats_the_place(text: str, city: str, state: str, zipcode: str) -> bool:
    # The city, state and ZIP again, often cut by the field: BALT MD, NEW, # LA, ANTA GA3034, FL USA.
    words = [word for word in re.sub(r"[#\d]", " ", text).split() if word not in ("USA", "US", state)]
    rest = " ".join(words)
    if not rest:
        return bool(re.sub(r"[#\d ]", "", text))
    return len(rest) >= 2 and (
        city.startswith(rest) or _get_initials(city) == rest or (len(rest) >= 4 and city.endswith(rest))
    )


def _split_street_words(street_1: str) -> list[str]:
    first_part, *later = [_strip_punctuation(part) for part in street_1.split(",")]
    start = first_part.split()[:2]
    starts_with_number = bool(start) and start[0].isdigit()
    kept = [
        part
        for part in later
        if not (len(part) >= 4 and (first_part.startswith(part) or (starts_with_number and part.split()[:2] == start)))
    ]
    return " ".join([first_part, *kept]).split()


def _drop_place_after_street(words: list[str], city: str, state: str, zipcode: str) -> list[str]:
    if len(city) < 4:
        return words
    place = f"{city} {state} {zipcode}"
    for place_start in range(3, len(words)):
        tail = " ".join(words[place_start:])
        if len(tail) < 4:
            continue
        if not (place.startswith(tail) or f"{city} {zipcode}".startswith(tail)):
            continue
        if len(tail) >= len(city) or words[place_start - 1] in STREET_TYPE_ABBREVIATIONS:
            return words[:place_start]
    return words


def _read_unnamed_second_line(street_2: str) -> tuple[str, str]:
    # A second line the parser cannot name: NUM 303 is a unit, a designator alone is nothing, the rest stays.
    alone = _strip_punctuation(street_2).replace("#", " # ").split()
    if len(alone) <= 2 and alone[0] in _UNIT_TYPES:
        return "", _build_unit(street_2)
    return "", _strip_punctuation(street_2)


def standard_street(street_1: str, street_2: str, city: str, state: str, zipcode: str) -> tuple[str, str]:
    """Return the two street lines in standard form: the street, then the unit."""
    if street_2 and city and _repeats_the_place(_strip_punctuation(street_2), city, state, zipcode):
        street_2 = ""
    # 4400 WEST 87TH TERRACE, 4400 WEST: the street typed twice
    words = _split_street_words(street_1)
    # 22649 SHAKER BLVD BEACHWOOD OH 441: the place typed after the street
    words = _drop_place_after_street(words, city, state, zipcode)
    # BOX 137 alone on the first line
    street_1 = re.sub(r"^(?:BOX|POB|P O B) (?=\d+$)", "PO BOX ", " ".join(words))
    first, second = parse_line(street_1), parse_line(street_2)
    if first is None and second and second[0] and _CARE_OF.match(street_1):
        return second  # C/O a company, then the street
    one = first if first is not None else (street_1, "")
    two = second if second is not None else _read_unnamed_second_line(street_2)
    if one[0] and two[0] and two[0] != one[0]:
        return one[0], " ".join(part for part in (one[1], *two) if part)  # a second street line
    units = dict.fromkeys((one[1], two[1]))
    return one[0] or two[0], " ".join(part for part in units if part)


def _spell_out_city(city: str) -> str:
    # ST LOUIS and FT WORTH in full; a last word with no vowel is a USPS short form (MOUNTAIN BRK, MAYFIELD HTS).
    words = [CITY_ABBREVIATIONS.get(word, word) for word in _strip_punctuation(city).split()]
    if len(words) > 1 and not set(words[-1]) & set("AEIOUY"):
        words[-1] = LONGHAND_STREET_TYPES.get(words[-1], words[-1])
    return " ".join(words)


def _keep_letters(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text)


def _is_shortening(short: str, full: str) -> bool:
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


def _find_full_zip_names(zipcode: str) -> list[str]:
    listed = [name for name in zip_place(zipcode[:5])[1] if set(name.split()[-1]) & set("AEIOUY")]
    listed = [_spell_out_city(name) for name in listed]
    # USPS lists its own short forms too (PARADISE VLY, SLC): only the full names are answers
    full_names = (
        name
        for name in listed
        if not any(_is_shortening(name, other) or _get_initials(other) == name for other in listed)
    )
    return list(dict.fromkeys(full_names))


def standard_city(city: str, zipcode: str) -> str:
    """Return the city in full, read through the USPS names of its ZIP: no short form, no slip of a letter."""
    full = _spell_out_city(city)
    names = _find_full_zip_names(zipcode)
    full_letters = _keep_letters(full)
    same_letters = [name for name in names if _keep_letters(name) == full_letters]
    if same_letters:
        return same_letters[0]
    shortened = [
        name
        for name in names
        if _is_shortening(full, name) or (" " in name and len(full) >= 2 and _get_initials(name) == full)
    ]
    if shortened:
        return max(shortened, key=len)
    allowed = 2 if len(full_letters) >= 10 else 1
    slipped = [
        name for name in names if len(full_letters) >= 5 and OSA.distance(_keep_letters(name), full_letters) <= allowed
    ]
    if slipped:
        return slipped[0] if len(slipped) == 1 else full
    # SANTA for SANTA FE: cut short
    cut_short = [name for name in names if len(full) >= 5 and name.startswith(full + " ")]
    if cut_short:
        return cut_short[0] if len(cut_short) == 1 else full
    return full


def standard_address(street_1: str, street_2: str, city: str, state: str, zipcode: str) -> tuple[str, ...]:
    """Return the standardized street, city, state and ZIP of one address."""
    # 12230 BONE HOLLOW ROAD KAMAS 84036: a city and its own ZIP typed in the street are the real place
    *words, last = _strip_punctuation(street_1).split() or [""]
    for size in (1, 2, 3):
        city_words = " ".join(words[-size:])
        if len(words) >= size + 2 and city_words in zip_place(last)[1]:
            street_1, city, zipcode, state = " ".join(words[:-size]), city_words, last, zip_place(last)[0]
            break
    city = _strip_punctuation(city)
    if state:
        # BETHESDA MARYLAND 20817, BOCA RATON FL: the state and ZIP typed after the city
        city = re.sub(rf"(?:\s+{re.escape(state)})?(?:\s+\d{{5}}(?:-?\d{{4}})?)?$", "", city)
    digits = re.sub(r"\D", "", zipcode)
    five = digits.zfill(5)[:5] if len(digits) <= 5 else digits.zfill(9)[:5]
    town = standard_city(city, five)
    return (*standard_street(street_1, street_2, town, state, five), town, state, zipcode)


def standardize_addresses(df: pd.DataFrame) -> int:
    """Rewrite every US address in its standard form; return how many filings changed."""
    us_rows = ~foreign_address_mask(df)
    if not us_rows.any():
        return 0
    columns = list(ADDRESS_FIELDS)
    before = df.loc[us_rows, columns].fillna("").astype(str)
    filed = before.apply(upper_text)
    distinct = filed.drop_duplicates()
    standard = {address: standard_address(*address) for address in distinct.itertuples(index=False, name=None)}
    after = pd.DataFrame([standard[address] for address in filed.itertuples(index=False, name=None)], index=filed.index)
    for position, column in enumerate(columns):
        df.loc[us_rows, column] = after[position].replace("", np.nan).astype(object)
    return int((before != df.loc[us_rows, columns].fillna("").astype(str)).any(axis=1).sum())


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
