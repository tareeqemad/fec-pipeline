"""One name per donor.

The surname is the one the donor files most. A given name is shown in its fullest spelling (MATTHEW over MATT),
a rare one-letter slip aside (ZIPORAH beside 87 ZIPORA). Another given name under the same donor (a spouse on a
joint account) keeps its own.
"""

import re
from collections import Counter, defaultdict

import pandas as pd
from rapidfuzz.distance import OSA

from fec.common.text import WORD_TOKEN_RE
from fec.matching.identity_rules import COMMITTEE_NAMES
from fec.matching.name_compare import NICKNAME_MAP

_ASIDE = re.compile(r"\([^()]*\)?")


def canonicalize_committee_names(df: pd.DataFrame) -> None:
    """Apply reviewed committee names and keys after the cleaning steps."""
    committees = df["entity_type"].eq("COMMITTEE/PAC") & df["identity_status"].eq("confirmed")
    filings = df["sub_id"].astype(str)
    for keep, drop, name, sub_ids in COMMITTEE_NAMES:
        selected = committees & df["donor_key"].isin((keep, drop))
        if sub_ids:
            selected &= filings.isin(sub_ids)
        df.loc[selected, "donor_key"] = keep
        df.loc[selected, "contributor_name"] = name


def _split_tokens(text: str) -> list:
    return WORD_TOKEN_RE.findall(text.upper())


def _strip_asides(first: str) -> str:
    text = " ".join(_ASIDE.sub(" ", first).replace(")", " ").split()).strip(" ,-")
    return re.sub(r"(?<=[A-Z]{3})\.+$", "", text)  # JOHN.


def _find_surname_root(variant: str, lasts: list[str], firsts: list[str], filed: Counter) -> str:
    # KARNEY GUREN beside GUREN: the same surname when the donor files KARNEY as a given name
    given = {token for last, first in zip(lasts, firsts) if last != variant for token in _split_tokens(first)}
    for shorter in sorted(filed, key=len):
        head = variant[: -len(shorter)]
        ends_with_shorter = shorter != variant and variant.endswith(shorter) and head[-1:] in (" ", ".")
        head_tokens = _split_tokens(head)
        if ends_with_shorter and head_tokens and all(len(token) == 1 or token in given for token in head_tokens):
            return _find_surname_root(shorter, lasts, firsts, filed)
    return variant


def _choose_surname(lasts: list[str], firsts: list[str]) -> tuple[str, str]:
    filed = Counter(last for last in lasts if last)
    roots = Counter()
    for variant, count in filed.items():
        roots[_find_surname_root(variant, lasts, firsts, filed)] += count
    surname = min(roots, key=lambda root: (-roots[root], root))
    initial, _, rest = surname.partition(" ")
    if (
        rest
        and len(initial.rstrip(".")) == 1
        and not (initial[0] in "OD" or (initial[0] == "L" and rest[0] in "AEIOUH"))
    ):
        return rest.strip(), initial
    return surname, ""


def _drop_repeated_asides(spelling: str, asides: set) -> str:
    first, *rest = _strip_asides(spelling).split() or [""]
    kept_words = (word for word in rest if not set(_split_tokens(word)) <= asides or not _split_tokens(word))
    return " ".join([first, *kept_words])


def _choose_fullest_spelling(spellings: list[str], asides: set, rare: frozenset) -> str | None:
    if not spellings:
        return None
    words = Counter(_split_tokens(spelling)[0] for spelling in spellings)
    slips = {
        word
        for word in words
        if len(word) >= 4
        and word in rare
        and any(words[other] > words[word] and OSA.distance(word, other) == 1 for other in words)
    }
    kept = [spelling for spelling in spellings if _split_tokens(spelling)[0] not in slips] or spellings
    filed = Counter(kept)

    rank = {}
    for spelling in filed:
        plain = _drop_repeated_asides(spelling, asides)
        rank[spelling] = (
            len("".join(_split_tokens(plain))),
            plain == spelling,
            len(spelling) if plain == spelling else 0,
            filed[spelling],
            -kept.index(spelling),
        )
    best = max(rank, key=rank.get)
    plain_best = _drop_repeated_asides(best, asides)
    balanced_aside = "(" in best and _strip_asides(best) == plain_best and best.count("(") == best.count(")")
    if plain_best == best or balanced_aside:
        return best
    return plain_best


def _drop_cut_surname(given: str, surname: str) -> str:
    head, _, last_word = given.rpartition(" ")
    if head and len(last_word) >= len(surname) - 2 and surname.startswith(last_word):
        return head
    return given


def _group_given_names(given: list[str], own: set, asides: set) -> dict:
    groups = defaultdict(list)
    for position, first in enumerate(given):
        tokens = _split_tokens(first)
        if not tokens:
            continue
        others = frozenset(token for token in tokens[1:] if len(token) > 1 and token not in own and token not in asides)
        groups[(NICKNAME_MAP.get(tokens[0], tokens[0])[0], others)].append(position)
    # P R beside P RICHARD: initials that a fuller filing spells out are the same name
    for key in sorted(groups, key=lambda group_key: len(group_key[1])):
        initials = {
            token for position in groups[key] for token in _split_tokens(given[position])[1:] if len(token) == 1
        }
        fuller = [
            other
            for other in groups
            if other[0] == key[0] and key[1] < other[1] and all(word[0] in initials for word in other[1] - key[1])
        ]
        if fuller:
            groups[max(fuller, key=lambda group_key: len(groups[group_key]))] += groups.pop(key)
    return groups


def donor_names(lasts: list[str], firsts: list[str], rare: frozenset = frozenset()) -> tuple[str, list]:
    """Return the donor's surname and, for each filing, the given name to show."""
    surname, initial = _choose_surname(lasts, firsts)
    surname_words = surname.split()
    own = {token for last in lasts for token in _split_tokens(last)} | set(surname_words)
    asides = {token for first in firsts for inner in re.findall(r"\(([^()]*)", first) for token in _split_tokens(inner)}
    # the surname typed again in the first-name box is no given name
    given = [" ".join(word for word in first.split() if word not in surname_words) for first in firsts]
    # GLENN STUART CHRYSTA: the surname again, cut short by the field
    given = [_drop_cut_surname(name, surname) for name in given]
    groups = _group_given_names(given, own, asides)
    everyone = _choose_fullest_spelling([name for name in given if name], asides, rare)
    shown = [everyone] * len(given)
    if len(groups) > 1:
        for positions in groups.values():
            name = _choose_fullest_spelling([given[position] for position in positions], asides, rare)
            for position in positions:
                shown[position] = name
    if initial:
        shown = [
            name if name and initial[0] in _split_tokens(name) else f"{name or ''} {initial}".strip() for name in shown
        ]
    return surname, shown


def canonicalize_donor_names(df: pd.DataFrame) -> None:
    """Give each donor one surname and each of their given names one spelling."""
    people = df[df["entity_type"].eq("INDIVIDUAL")]
    first_names = people["contributor_first_name"].fillna("").astype(str).str.upper()
    words = Counter(name_words[0] for name_words in first_names.str.split() if name_words)
    rare = frozenset(word for word, count in words.items() if count <= 2)
    for rows_index in people.groupby("donor_key").groups.values():
        rows = df.loc[rows_index]
        lasts = [value.strip() if isinstance(value, str) else "" for value in rows["contributor_last_name"]]
        if not any(lasts):
            continue
        firsts = [value.strip() if isinstance(value, str) else "" for value in rows["contributor_first_name"]]
        surname, given = donor_names(lasts, firsts, rare)
        df.loc[rows_index, "contributor_last_name"] = surname
        df.loc[rows_index, "contributor_first_name"] = pd.Series(given, index=rows_index, dtype=object)
        df.loc[rows_index, "contributor_name"] = [f"{surname}, {name}" if name else surname for name in given]
