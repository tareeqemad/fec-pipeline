"""Joint filings: first names carrying a co-filer."""

import re

from fec.donor_match.constants import NAME_SUFFIXES, NICKNAME_MAP

# a parenthesized aside to drop: "(NICKNAME)", "(unclosed"
_PAREN_RE = re.compile(r"\([^()]*\)?")
# letter runs: words of a name field
_TOKEN_RE = re.compile(r"[A-Z]+")


# first-name words, without brackets or suffixes
def given_tokens(first: str) -> tuple:
    text = _PAREN_RE.sub(" ", str(first or "").upper())
    return tuple(t for t in _TOKEN_RE.findall(text) if t not in NAME_SUFFIXES)


# first-name part of 'LAST, FIRST' composite
def first_of_name(name: str) -> str:
    text = str(name or "")
    return text.split(",", 1)[1] if "," in text else ""


# nickname root form of a token
def _root(token: str) -> str:
    return NICKNAME_MAP.get(token, token)


# spelling words that could name a partner
def _partner_words(spelling: tuple, own_firsts: set) -> list:
    first = spelling[0]
    words = [w for w in spelling[1:] if len(w) >= 2]
    if len(spelling) == 1:
        # glued own name plus partner, maybe "AND"
        for base in own_firsts:
            if base != first and len(base) >= 2 and first.startswith(base):
                rest = first[len(base):]
                if rest.startswith("AND") and len(rest) >= 5:
                    words.append(rest[3:])
                if len(rest) >= 2:
                    words.append(rest)
    return words


# a spelling is this initial plus word
def _initial_form(initial: str, word: str, spellings) -> bool:
    return any(
        len(other) >= 2 and other[0] == initial and _root(other[1]) == _root(word)
        for other in spellings
    )


# word names the filer, not a co-filer
def _is_own_word(first: str, word: str, spellings) -> bool:
    return _initial_form(first[0], word, spellings) or _initial_form(word[0], first, spellings)


# co-filer name roots in a first-name spelling
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
        # partner must file without the filer's name
        files_alone = any(
            _root(other[0]) == root
            and all(_root(t) != _root(first) for t in other[1:])
            for other in household
            if other
        )
        if files_alone and not _is_own_word(first, word, own | household):
            partners.add(root)
    return frozenset(partners)
