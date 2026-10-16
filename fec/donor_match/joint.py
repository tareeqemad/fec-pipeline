"""Identify joint filings from household records and keep them separate."""

import re
from collections import defaultdict

from fec.donor_match.keys import _donor_keys
from fec.donor_match.rules import joint_name_exempt, resolve_donor_key
from fec.donor_match.normalize import NAME_SUFFIXES, NICKNAME_MAP


# a parenthesized aside to drop: "(NICKNAME)", "(unclosed"
_PAREN_RE = re.compile(r"\([^()]*\)?")
# a run of letters, to split a name field into words
_TOKEN_RE = re.compile(r"[A-Z]+")


def given_tokens(first: str) -> tuple:
    """The words of a first-name field, brackets and suffixes left out."""
    text = _PAREN_RE.sub(" ", str(first or "").upper())
    return tuple(t for t in _TOKEN_RE.findall(text) if t not in NAME_SUFFIXES)


def first_of_name(name: str) -> str:
    """The first-name part of a 'LAST, FIRST' composite ('' when there is no comma)."""
    text = str(name or "")
    return text.split(",", 1)[1] if "," in text else ""


# resolve a token to its nickname root form
def _root(token: str) -> str:
    return NICKNAME_MAP.get(token, token)


def _partner_words(spelling: tuple, own_firsts: set) -> list:
    """Words of one spelling that could name a second person."""
    first = spelling[0]
    words = [w for w in spelling[1:] if len(w) >= 2]
    if len(spelling) == 1:
        # glued: the filer's own first name plus a second name, maybe "AND"
        for base in own_firsts:
            if base != first and len(base) >= 2 and first.startswith(base):
                rest = first[len(base):]
                if rest.startswith("AND") and len(rest) >= 5:
                    words.append(rest[3:])
                if len(rest) >= 2:
                    words.append(rest)
    return words


def _initial_form(initial: str, word: str, spellings) -> bool:
    """Some spelling is this initial followed by this word ("L ROGER")."""
    return any(
        len(other) >= 2 and other[0] == initial and _root(other[1]) == _root(word)
        for other in spellings
    )


# Use the household's initial and full-name spellings to recognize a filer's own middle name.
def _is_own_word(first: str, word: str, spellings) -> bool:
    return _initial_form(first[0], word, spellings) or _initial_form(word[0], first, spellings)


# Find co-filer names using this filer's spellings and other household members' spellings.
def joint_partners(spelling: tuple, own_spellings, household_spellings) -> frozenset:
    if not spelling or len(spelling[0]) == 1:
        return frozenset()
    first = spelling[0]
    own = set(own_spellings)
    household = set(household_spellings)
    own_firsts = {s[0] for s in own if s}
    partners = set()
    for word in _partner_words(spelling, own_firsts):
        root = _root(word)
        if root == _root(first):
            continue
        # the partner must file on their own: a household spelling that starts
        # with the word and does not also carry the filer's first name
        files_alone = any(
            _root(other[0]) == root
            and all(_root(t) != _root(first) for t in other[1:])
            for other in household
            if other
        )
        if files_alone and not _is_own_word(first, word, own | household):
            partners.add(root)
    return frozenset(partners)


# get a profile's given-name token spelling
def _profile_spelling(profile: dict) -> tuple:
    return given_tokens(first_of_name(profile["name"]))


# get a profile's normalized surname
def _profile_last(profile: dict) -> str:
    return profile["norm_name"].partition("|")[0] if "|" in profile["norm_name"] else ""


# Find profiles naming another person in the same surname and address/ZIP household.
# Apply verified merges and own-name exemptions first.
def find_joint_filings(profiles: dict, components: dict) -> dict:
    rid_to_key = _donor_keys(components)
    person_of = {rid: resolve_donor_key(key) for rid, key in rid_to_key.items()}
    members = defaultdict(list)
    for rid, person in person_of.items():
        members[person].append(rid)

    spellings = {rid: _profile_spelling(p) for rid, p in profiles.items()}
    by_place = defaultdict(set)  # (surname, street or zip5) -> persons
    places = defaultdict(set)    # person -> streets and zip5s
    for rid, person in person_of.items():
        profile = profiles[rid]
        last = _profile_last(profile)
        spots = {("S", street) for street in profile["streets"]}
        if len(profile["zip5"]) == 5:
            spots.add(("Z", profile["zip5"]))
        places[person] |= spots
        if last:
            for spot in spots:
                by_place[(last, spot)].add(person)

    signatures = {}
    for rid, person in person_of.items():
        spelling = spellings[rid]
        # a one-word name can only be a glued pair ("ELLENSTU") of a filer
        # who also files under another spelling
        if not spelling or (len(spelling) < 2 and len(members[person]) < 2):
            continue
        profile = profiles[rid]
        if joint_name_exempt(profile["name"]):
            continue
        last = _profile_last(profile)
        others = set()
        for spot in places[person]:
            others |= by_place.get((last, spot), set())
        others.discard(person)
        if not others:
            continue
        household = {
            spellings[other_rid]
            for other in others
            for other_rid in members[other]
            if _profile_last(profiles[other_rid]) == last
        }
        own = {spellings[member] for member in members[person]}
        partners = joint_partners(spelling, own, household)
        if partners:
            signatures[rid] = partners
    return signatures


# Separate joint filings by co-filer pair, preserving the solo matches.
# Keep the original root for its part; key other parts by their most-filed profile.
def split_joint_filings(components: dict, signatures: dict, profiles: dict) -> int:
    moved = 0
    for root, members in list(components.items()):
        parts = defaultdict(set)
        for rid in members:
            parts[signatures.get(rid, frozenset())].add(rid)
        if len(parts) < 2:
            continue
        del components[root]
        for signature, part in sorted(parts.items(), key=lambda item: sorted(item[0])):
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
