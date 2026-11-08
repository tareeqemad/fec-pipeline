"""Identify joint filings from household records and keep them separate."""

import re
from collections import defaultdict

from fec.donor_match.keys import _donor_keys
from fec.donor_match.rules import MERGED_GIVEN_NAMES, MERGED_NAME_PREFIXES, _name_words, resolve_donor_key
from fec.donor_match.normalize import NAME_SUFFIXES, NICKNAME_MAP


# a parenthesized aside to drop: "(NICKNAME)", "(unclosed"
_PAREN_RE = re.compile(r"\([^()]*\)?")
# A run of letters, to split names into words.
_TOKEN_RE = re.compile(r"[A-Z]+")


def given_tokens(first: str) -> tuple:
    """First-name words, without brackets and suffixes."""
    text = _PAREN_RE.sub(" ", str(first or "").upper())
    return tuple(t for t in _TOKEN_RE.findall(text) if t not in NAME_SUFFIXES)


def first_of_name(name: str) -> str:
    """First-name part of 'LAST, FIRST', or '' without a comma."""
    text = str(name or "")
    return text.split(",", 1)[1] if "," in text else ""


# resolve a token to its nickname root form
def _root(token: str) -> str:
    return NICKNAME_MAP.get(token, token)


def joint_name_exempt(name: str) -> bool:
    """A verified merge says these given names belong to one person."""
    last, words = _name_words(name)
    if not last or not words:
        return False
    if f"{last}, {' '.join(words)}".startswith(MERGED_NAME_PREFIXES):
        return True
    roots = {_root(w) for w in words if len(w) >= 2}
    return any(roots <= {_root(w) for w in group} for group in MERGED_GIVEN_NAMES.get(last, ()))


def _partner_words(spelling: tuple, own_firsts: set) -> list:
    """Words of one spelling that could name a second person."""
    first = spelling[0]
    words = [w for w in spelling[1:] if len(w) >= 2]
    if len(spelling) == 1:
        # Glued: the filer's first name plus a second name.
        for base in own_firsts:
            if base != first and len(base) >= 2 and first.startswith(base):
                rest = first[len(base):]
                if rest.startswith("AND") and len(rest) >= 5:
                    words.append(rest[3:])
                if len(rest) >= 2:
                    words.append(rest)
    return words


def _initial_form(initial: str, word: str, spellings) -> bool:
    """Some spelling is this initial plus this word ("L ROGER")."""
    return any(
        len(other) >= 2 and other[0] == initial and _root(other[1]) == _root(word)
        for other in spellings
    )


# Household spellings reveal a filer's own middle name.
def _is_own_word(first: str, word: str, spellings) -> bool:
    return _initial_form(first[0], word, spellings) or _initial_form(word[0], first, spellings)


# The people a joint filing names, filer included; empty for one person.
def joint_partners(spelling: tuple, own_spellings, household_spellings) -> frozenset:
    if not spelling or len(spelling[0]) == 1:
        return frozenset()
    first = spelling[0]
    own = set(own_spellings)
    household = set(household_spellings)
    own_firsts = {s[0] for s in own if s}
    everyone = own | household
    # Glued (STUARTANDSARA): the filer is the first name it starts with.
    filer = first if len(spelling) > 1 else next(
        (b for b in own_firsts if b != first and len(b) >= 2 and first.startswith(b)), first,
    )
    partners = set()
    for word in _partner_words(spelling, own_firsts):
        root = _root(word)
        if root == _root(filer):
            continue
        # Two people, each filing alone here as well: STUART and SARA
        # beside STUART SARA. MENACHEM ALBERT beside ALBERT alone is one
        # person with two given names.
        if (_files_alone(root, filer, everyone) and _files_alone(_root(filer), word, everyone)
                and not _is_own_word(first, word, everyone)):
            partners.add(root)
    return frozenset(partners | {_root(filer)}) if partners else frozenset()


def _files_alone(root: str, without: str, spellings) -> bool:
    """Some spelling starts with this name and leaves the other out."""
    return any(
        _root(other[0]) == root and all(_root(t) != _root(without) for t in other[1:])
        for other in spellings
        if other
    )


# get a profile's given-name token spelling
def _profile_spelling(profile: dict) -> tuple:
    return given_tokens(first_of_name(profile["name"]))


# get a profile's normalized surname
def _profile_last(profile: dict) -> str:
    return profile["norm_name"].partition("|")[0] if "|" in profile["norm_name"] else ""


# Profiles naming another person in the same household, by the people named.
def find_joint_filings(profiles: dict, components: dict) -> dict:
    rid_to_key = _donor_keys(components)
    person_of = {rid: resolve_donor_key(key) for rid, key in rid_to_key.items()}
    members = defaultdict(list)
    for rid, person in person_of.items():
        members[person].append(rid)

    spellings = {rid: _profile_spelling(p) for rid, p in profiles.items()}
    by_place = defaultdict(set)  # (surname, street or zip5) -> filings there
    for rid, profile in profiles.items():
        last = _profile_last(profile)
        spots = {("S", street) for street in profile["streets"]}
        if len(profile["zip5"]) == 5:
            spots.add(("Z", profile["zip5"]))
        if last:
            for spot in spots:
                by_place[(last, spot)].add(rid)

    signatures = {}
    for rid, person in person_of.items():
        spelling = spellings[rid]
        # A one-word name is a glued pair only with alternatives.
        if not spelling or (len(spelling) < 2 and len(members[person]) < 2):
            continue
        profile = profiles[rid]
        if joint_name_exempt(profile["name"]):
            continue
        # The household: everyone with the surname at this filing's address.
        last = _profile_last(profile)
        household = set()
        for spot in {("S", street) for street in profile["streets"]} | {("Z", profile["zip5"])}:
            household |= by_place.get((last, spot), set())
        household.discard(rid)
        if not household:
            continue
        household = {spellings[other] for other in household}
        own = {spellings[member] for member in members[person]}
        people = joint_partners(spelling, own, household)
        if people:
            signatures[rid] = people
    return signatures


# Separate joint filings by the people they name, keeping solo matches.
def split_joint_filings(components: dict, signatures: dict, profiles: dict, edges=()) -> int:
    """A couple's joint filings get one group of their own.

    edges are the matched pairs. A joint filing matched both partners'
    solo filings and so bridged two people: once it leaves, the solo
    filings stay together only where they matched each other, or through
    a joint filing on the same person's side (MARC and MARC MELISSA).
    """
    moved = 0
    for root, members in list(components.items()):
        parts = defaultdict(set)
        for rid in members:
            parts[signatures.get(rid, frozenset())].add(rid)
        if len(parts) < 2:
            continue
        del components[root]
        groups = [(signature, part) for signature, part in parts.items() if signature]
        solo = _connected(parts[frozenset()], signatures, profiles, edges)
        groups += [(frozenset(), part) for part in solo]
        for signature, part in sorted(groups, key=lambda item: (sorted(item[0]), sorted(item[1]))):
            if root in part:
                new_root = root
            else:
                ranked = sorted(
                    part,
                    key=lambda rid: (profiles[rid]["record_count"], rid),
                    reverse=True,
                )
                new_root = next(rid for rid in ranked if rid not in components)
            components[new_root] = part
            if signature:
                moved += len(part)
    return moved


def _connected(solo: set, signatures: dict, profiles: dict, edges) -> list[set]:
    """The groups the solo filings form through their matches."""
    parent = {rid: rid for rid in solo}

    def find(rid):
        while parent[rid] != rid:
            parent[rid] = parent[parent[rid]]
            rid = parent[rid]
        return rid

    def first_root(rid):
        return _root(profiles[rid]["norm_name"].partition("|")[2])

    through = defaultdict(set)  # (joint filing, person) -> that person's solo filings
    for rid_a, rid_b in edges:
        if rid_a in parent and rid_b in parent:
            parent[find(rid_a)] = find(rid_b)
        for joint, other in ((rid_a, rid_b), (rid_b, rid_a)):
            if joint in signatures and other in parent and first_root(other) in signatures[joint]:
                through[(joint, first_root(other))].add(other)
    for linked in through.values():
        first, *rest = sorted(linked)
        for other in rest:
            parent[find(other)] = find(first)
    groups = defaultdict(set)
    for rid in solo:
        groups[find(rid)].add(rid)
    return list(groups.values())
