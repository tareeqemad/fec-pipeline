"""Compare a filed street with its cleaned form, part by part.

Formatting is not a change: STREET and ST, NORTH and N, a unit written in
street_1 or in street_2, a house number typed with a space (10 17 -> 1017).
What can move the place is reported per part: house number, direction,
street type, and the unit number changing, appearing or disappearing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ABBREVIATIONS = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR", "BOULEVARD": "BLVD", "LANE": "LN",
    "COURT": "CT", "PLACE": "PL", "CIRCLE": "CIR", "TERRACE": "TER", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "TRAIL": "TRL", "SQUARE": "SQ", "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW", "APARTMENT": "APT",
    "SUITE": "STE", "FLOOR": "FL", "POBOX": "PO BOX", "BUILDING": "BLDG", "NUMBER": "#",
    "CROSSING": "XING", "HEIGHTS": "HTS", "POINT": "PT", "PLAZA": "PLZ", "BEND": "BND", "SO": "S", "SUTE": "STE", "STR": "ST",
    "LA": "LN", "AV": "AVE", "AVNUE": "AVE",
}
STREET_TYPES = frozenset({
    "ST", "AVE", "RD", "DR", "BLVD", "LN", "CT", "PL", "CIR", "TER", "PKWY", "HWY", "TRL", "SQ", "WAY",
    "LOOP", "RUN", "PT", "PATH", "PLZ", "XING", "ROW", "BND", "HTS",
})
DIRECTIONS = frozenset({"N", "S", "E", "W", "NE", "NW", "SE", "SW"})
_UNIT_WORDS = r"APT|STE|UNIT|FL|RM|PH|BLDG|LOT|SPC|PMB"
# the first unit word or # in a street: "12 MAIN ST APT 5", "12 MAIN ST # 5"
_UNIT_START_RE = re.compile(rf"(?:\b(?:{_UNIT_WORDS})\b|#)")
# an ordinal's suffix: "5TH" -> "5"
_ORDINAL_RE = re.compile(r"\b(\d+)(?:ST|ND|RD|TH)\b")
# a PO box and its number, however written: "PO BOX 123", "POB 123", "PO 123", "BOX 123"
_PO_BOX_RE = re.compile(r"^(?:P ?O ?(?:BOX|B)?|BOX) ?(\d\S*)")
# a unit number after the street type with no unit word: "155 STEELE ST 616", "5500 ISLAND DR 601 N"
_BARE_UNIT_RE = re.compile(r"\b(?:ST|AVE|RD|DR|BLVD|LN|CT|PL|CIR|TER|PKWY|WAY|TRL|SQ) (\d+(?: [A-Z])?)$")

CHANGE_KINDS = (
    "house_number_changed", "street_direction_changed", "street_type_changed", "street_type_added",
    "unit_changed", "unit_removed", "unit_added", "po_box_number_changed", "street_replaced",
)


# uppercase abbreviated tokens: "123 North Main Street." -> "123 N MAIN ST"
def normalize(text) -> str:
    text = str(text or "").upper().replace("&", " AND ")
    text = re.sub(r"[\[\]]", "", text)  # AP[T 503W -> APT 503W
    text = re.sub(r"[.,;:'\"()]", " ", text)
    text = re.sub(r"#\s*", " # ", text)
    text = re.sub(r"(?<=[A-Z0-9])-(?=[A-Z0-9])", "", text)  # 139-04 -> 13904, T-8 -> T8
    text = _ORDINAL_RE.sub(r"\1", text)
    text = re.sub(r"(?<=\d)(?=[A-Z])|(?<=[A-Z])(?=\d)", " ", text)  # E72 -> E 72
    text = " ".join(_ABBREVIATIONS.get(token, token) for token in text.split())
    text = re.sub(r"\b([NS]) ([EW])\b", r"\1\2", text)  # N.W. -> NW
    text = re.sub(r"\b(\d+) (?:FL|FLO|FLOO|FLR)\b", r"FL \1", text)  # 19TH FLOOR -> FL 19
    text = re.sub(r"\bP [O0] BOX\b", "PO BOX", text)  # P.0. BOX
    return re.sub(r"\s+", " ", text).strip()


# each unit number in a text: "STE 105 # 105" -> ["105"]
def _unit_numbers(text: str) -> list[str]:
    numbers = []
    for segment in _UNIT_START_RE.split(f" {text} ")[1:] or ([text] if text else []):
        tokens = segment.split()
        if not tokens:
            continue
        kept = [tokens[0]]
        for token in tokens[1:]:  # APH 1, 3 L; a city word ends the unit: 304 PACIF
            if not (token.isdigit() or len(token) == 1):
                break
            kept.append(token)
        number = re.sub(r"[^A-Z0-9]", "", "".join(kept))
        half = number[:len(number) // 2]
        if number and half * 2 == number:  # written twice: 25J 25J
            number = half
        # a unit has a digit or is one letter; "CA9260" or "GA3034" is a state and ZIP
        if (any(ch.isdigit() for ch in number) or len(number) == 1) \
                and not re.fullmatch(r"[A-Z]{2,}\d{3,}", number):
            numbers.append(number)
    return list(dict.fromkeys(numbers))


# the street without its unit, and the unit number
def street_and_unit(street_1, street_2) -> tuple[str, str]:
    street, second = normalize(street_1), normalize(street_2)
    first = ""
    leading = re.match(rf"^(?:{_UNIT_WORDS}|#) (\S+) (\d.*)$", street)  # UNIT 8606 7485 VICTORY LN
    if leading:
        street, first = leading.group(2), f"# {leading.group(1)}"
    match = _UNIT_START_RE.search(street)
    if match:
        street, first = street[:match.start()].strip(), f"{first} {street[match.start():]}".strip()
    bare = _BARE_UNIT_RE.search(street)
    house = re.match(r"\d+", street)
    if bare and not first and not bare.group(1).startswith("0") \
            and not (house and bare.group(1).split()[0] == house.group()):  # 705 N BEDFORD DR 705 N
        street, first = street[:bare.start(1)].strip(), bare.group(1)
    if not second and not first:
        return street, ""
    units = _unit_numbers(first) if first and _UNIT_START_RE.match(first) else ([re.sub(r"\s", "", first)] if first else [])
    units += _unit_numbers(second if _UNIT_START_RE.match(second) else f"# {second}") if second else []
    return street, "".join(dict.fromkeys(units))  # the same unit in both fields counts once


@dataclass(frozen=True)
class StreetParts:
    house: str
    directions: frozenset
    name: tuple
    street_type: str
    po_box: str


# split a unit-free street into house, directions, name and type
def parse_street(street: str) -> StreetParts:
    po_box = _PO_BOX_RE.match(street)
    if po_box:
        return StreetParts("", frozenset(), (), "", po_box.group(1))
    tokens = street.split()
    house = ""
    while tokens and tokens[0].isdigit():
        house += tokens.pop(0)
        # 10 17 GREENTREE -> 1017, but 6620 24 ST keeps 24 as the street name
        if not (len(tokens) > 1 and tokens[0].isdigit() and tokens[1] not in STREET_TYPES | DIRECTIONS):
            break
    house = house.lstrip("0") or house
    typed = [i for i, token in enumerate(tokens) if token in STREET_TYPES]
    if typed and typed[-1] < len(tokens) - 1 and not all(t in DIRECTIONS for t in tokens[typed[-1] + 1:]):
        tokens = tokens[:typed[-1] + 1]  # a city after the street: MEADOW RIDGE WAY ENCINO
    pre = []
    while tokens and tokens[0] in DIRECTIONS:
        pre.append(tokens.pop(0))
    post = []
    while tokens and tokens[-1] in DIRECTIONS:
        post.insert(0, tokens.pop())
    street_type = tokens.pop() if tokens and tokens[-1] in STREET_TYPES else ""
    if not tokens and (pre or post):  # the direction is the name: 123 NORTH ST
        tokens, pre, post = pre + post, [], []
    elif not tokens and street_type:  # the type is the name: 7 LOOP
        tokens, street_type = [street_type], ""
    return StreetParts(house, frozenset(pre + post), tuple(tokens), street_type, "")


# the cleaned street appears word for word inside the filed one
def _found_in(cleaned: str, filed: str) -> bool:
    return cleaned != filed and f" {cleaned} " in f" {filed} "


# the street from its first number: "W RES 210 LAVACA" -> "210 LAVACA"
def _from_first_number(street: str) -> str:
    if _PO_BOX_RE.match(street):
        return street
    match = re.search(r"(?:^| )(\d.*)$", street)
    return match.group(1) if match else street


# a filed street that is only a number: "2655"
def _is_bare_number(street: str) -> bool:
    parts = parse_street(_from_first_number(street))
    return not parts.po_box and not parts.name


# the parts that changed between filed and cleaned street
def street_changes(filed_1, filed_2, cleaned_1, cleaned_2) -> list[str]:
    filed_street, filed_unit = street_and_unit(filed_1, filed_2)
    cleaned_street, cleaned_unit = street_and_unit(cleaned_1, cleaned_2)
    changes = []
    if filed_street == cleaned_street:
        pass  # the same street once formatting is set aside
    elif filed_street and cleaned_street and _found_in(cleaned_street, filed_street):
        pass  # the cleaned street was read out of the filed text: "C/O AEDER, 52 GREENTREE DR"
    elif cleaned_street and (not any(ch.isdigit() for ch in filed_street) or "@" in filed_street
                             or _is_bare_number(filed_street)):
        changes.append("street_replaced")  # the filing named no street: an email, a name, a bare number
    elif filed_street and cleaned_street:
        before, after = parse_street(_from_first_number(filed_street)), parse_street(cleaned_street)
        if before.po_box or after.po_box:
            if before.po_box != after.po_box:
                changes.append("po_box_number_changed")
        else:
            if before.house != after.house:
                changes.append("house_number_changed")
            if before.directions != after.directions:
                changes.append("street_direction_changed")
            glued = "".join(before.name) + before.street_type == "".join(after.name) + after.street_type
            if before.street_type != after.street_type and not glued:  # 2425 LST NW = 2425 L ST NW
                # a type the filing never wrote is a guess between streets: ISENGARD ST or DR
                changes.append("street_type_changed" if before.street_type else "street_type_added")
    if filed_unit and not cleaned_unit:
        changes.append("unit_removed")
    elif cleaned_unit and not filed_unit:
        changes.append("unit_added")
    elif filed_unit != cleaned_unit:
        changes.append("unit_changed")
    return changes
