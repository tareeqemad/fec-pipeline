"""Joint filings: a first-name field that also carries another person's given name.

A filer sometimes puts two people on one filing: "SPELLMAN, MARC MELISSA"
(Marc and Melissa), or glued, "MORRIS, ELLENSTU" (Ellen and Stu). Each person
keeps their own name: the joint row stays as filed, on its own donor, and the
partner's name is never written onto the filer's solo filings.

The second word is treated as a co-filer only on the household's own evidence:
another person with the same surname, who shares a street or a ZIP with the
filer, files under that given name WITHOUT the filer's name (Melissa Spellman
files alone at the same street). It is the filer's own middle name instead when

* the filer's "first name" is only an initial ("M FREDDIE"),
* the word is a nickname of the first name itself,
* the household also files the filer's first initial plus the word ("L ROGER"
  next to "LOWELL ROGER", "Y. DAVID" next to "YEHUDA DAVID"), or the word's
  initial plus the first name ("M STEPHEN" next to "STEVE MARVIN": one man
  who orders Marvin and Stephen both ways),
* or a verified identity rule already joins the two spellings (see
  rules.joint_name_exempt).

Nothing here is a word list: the same word is a co-filer in one household and
a middle name in another.
"""

import re

from .constants import NAME_SUFFIXES, NICKNAME_MAP

_PAREN_RE = re.compile(r"\([^()]*\)?")
_TOKEN_RE = re.compile(r"[A-Z]+")


def given_tokens(first: str) -> tuple:
    """The words of a first-name field, brackets and suffixes left out."""
    text = _PAREN_RE.sub(" ", str(first or "").upper())
    return tuple(t for t in _TOKEN_RE.findall(text) if t not in NAME_SUFFIXES)


def first_of_name(name: str) -> str:
    """The first-name part of a 'LAST, FIRST' composite ('' when there is no comma)."""
    text = str(name or "")
    return text.split(",", 1)[1] if "," in text else ""


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


def _is_own_word(first: str, word: str, spellings) -> bool:
    """The household files the word as the filer's own name, or the filer's
    name as the word-person's own: one person, not two.

    "L ROGER" next to "LOWELL ROGER": Roger is Lowell's middle name.
    "M STEPHEN" next to "STEVE MARVIN": the Marvin who files alone is Marvin
    Stephen, i.e. the same two names the other way round. A couple's joint
    filings in both orders ("STUART SARA", "SARA STUART") carry no such
    initial form and stay joint.
    """
    return _initial_form(first[0], word, spellings) or _initial_form(word[0], first, spellings)


def joint_partners(spelling: tuple, own_spellings, household_spellings) -> frozenset:
    """Given-name roots of the co-filers a first-name spelling carries (empty for a solo filing).

    own_spellings: every first-name spelling of this filer's own records.
    household_spellings: the spellings of OTHER people with the same surname
    who share a street or ZIP with the filer.
    """
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
