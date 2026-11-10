"""One name per donor.

The surname is the one the donor files most. A given name is shown in its
fullest spelling (MATTHEW over MATT), a rare one-letter slip aside
(ZIPORAH beside 87 ZIPORA). Filings with a different given name under the
same donor (a spouse on a joint account) keep their own.
"""

import re
from collections import Counter, defaultdict

import pandas as pd
from rapidfuzz.distance import OSA

from fec.donor_match.normalize import NICKNAME_MAP
from fec.donor_match.rules import COMMITTEE_NAMES

_TOKEN = re.compile(r"[A-Z0-9]+")
_ASIDE = re.compile(r"\([^()]*\)?")


def canonicalize_committee_names(df: pd.DataFrame) -> int:
    """Apply reviewed committee names and keys after the cleaning steps."""
    committees = df["entity_type"].eq("COMMITTEE/PAC") & df["identity_status"].eq("confirmed")
    filings = df["sub_id"].astype(str)
    total = 0
    for keep, drop, name, sub_ids in COMMITTEE_NAMES:
        selected = committees & df["donor_key"].isin((keep, drop))
        if sub_ids:
            selected &= filings.isin(sub_ids)
        changed = selected & (df["donor_key"].ne(keep) | df["contributor_name"].ne(name))
        df.loc[changed, "donor_key"] = keep
        df.loc[changed, "contributor_name"] = name
        total += int(changed.sum())
    return total


def _tokens(text: str) -> list:
    """Return the name tokens of a text, uppercased."""
    return _TOKEN.findall(text.upper())


def _core(first: str) -> str:
    """Return a given name without what the filer wrote in brackets."""
    text = " ".join(_ASIDE.sub(" ", first).replace(")", " ").split()).strip(" ,-")
    return re.sub(r"(?<=[A-Z]{3})\.+$", "", text)  # JOHN.


def _surname(lasts: list[str], firsts: list[str]) -> tuple[str, str]:
    """Return the most filed surname, and an initial typed before it (W. HAHN)."""
    filed = Counter(last for last in lasts if last)

    def base(variant: str) -> str:
        """Return the surname a variant shortens to when its extra words are initials or given names."""
        # KARNEY GUREN beside GUREN: the same surname when the donor files KARNEY as a given name
        given = {t for last, first in zip(lasts, firsts) if last != variant for t in _tokens(first)}
        for shorter in sorted(filed, key=len):
            head = variant[: -len(shorter)]
            if shorter != variant and variant.endswith(shorter) and head[-1:] in (" ", ".") and _tokens(head):
                if all(len(t) == 1 or t in given for t in _tokens(head)):
                    return base(shorter)
        return variant

    roots = Counter()
    for variant, count in filed.items():
        roots[base(variant)] += count
    surname = min(roots, key=lambda v: (-roots[v], v))
    initial, _, rest = surname.partition(" ")
    if (
        rest
        and len(initial.rstrip(".")) == 1
        and not (initial[0] in "OD" or (initial[0] == "L" and rest[0] in "AEIOUH"))
    ):
        return rest.strip(), initial
    return surname, ""


def _fullest(spellings: list[str], asides: set, rare: frozenset) -> str | None:
    """Return the fullest spelling; among equals the most filed, then the first filed."""
    if not spellings:
        return None
    words = Counter(_tokens(s)[0] for s in spellings)
    slips = {
        w
        for w in words
        if len(w) >= 4 and w in rare and any(words[o] > words[w] and OSA.distance(w, o) == 1 for o in words)
    }
    kept = [s for s in spellings if _tokens(s)[0] not in slips] or spellings
    filed = Counter(kept)

    def plain(s: str) -> str:
        """Return a given name without bracketed asides and the words that only repeat them."""
        first, *rest = _core(s).split() or [""]
        return " ".join([first, *(w for w in rest if not set(_tokens(w)) <= asides or not _tokens(w))])

    rank = {
        s: (len("".join(_tokens(plain(s)))), plain(s) == s, len(s) if plain(s) == s else 0, filed[s], -kept.index(s))
        for s in filed
    }
    best = max(rank, key=rank.get)
    return (
        best
        if plain(best) == best or "(" in best and _core(best) == plain(best) and best.count("(") == best.count(")")
        else plain(best)
    )


def donor_names(lasts: list[str], firsts: list[str], rare: frozenset = frozenset()) -> tuple[str, list]:
    """Return the donor's surname and, for each filing, the given name to show."""
    surname, initial = _surname(lasts, firsts)
    own = {t for last in lasts for t in _tokens(last)} | set(surname.split())
    asides = {t for first in firsts for inner in re.findall(r"\(([^()]*)", first) for t in _tokens(inner)}
    # the surname typed again in the first-name box is no given name
    given = [" ".join(w for w in first.split() if w not in surname.split()) for first in firsts]
    # GLENN STUART CHRYSTA: the surname again, cut short by the field
    given = [
        g.rsplit(" ", 1)[0]
        if " " in g and len(g.split()[-1]) >= len(surname) - 2 and surname.startswith(g.split()[-1])
        else g
        for g in given
    ]
    groups = defaultdict(list)
    for position, first in enumerate(given):
        tokens = _tokens(first)
        if tokens:
            others = frozenset(t for t in tokens[1:] if len(t) > 1 and t not in own and t not in asides)
            groups[(NICKNAME_MAP.get(tokens[0], tokens[0])[0], others)].append(position)
    # P R beside P RICHARD: initials that a fuller filing spells out are the same name
    for key in sorted(groups, key=lambda k: len(k[1])):
        initials = {t for p in groups[key] for t in _tokens(given[p])[1:] if len(t) == 1}
        fuller = [
            k for k in groups if k[0] == key[0] and key[1] < k[1] and all(n[0] in initials for n in k[1] - key[1])
        ]
        if fuller:
            groups[max(fuller, key=lambda k: len(groups[k]))] += groups.pop(key)
    everyone = _fullest([g for g in given if g], asides, rare)
    shown = [everyone] * len(given)
    if len(groups) > 1:
        for positions in groups.values():
            name = _fullest([given[p] for p in positions], asides, rare)
            for p in positions:
                shown[p] = name
    if initial:
        shown = [name if name and initial[0] in _tokens(name) else f"{name or ''} {initial}".strip() for name in shown]
    return surname, shown


def canonicalize_donor_names(df: pd.DataFrame) -> None:
    """Give each donor one surname and each of their given names one spelling."""
    people = df[df["entity_type"].eq("INDIVIDUAL")]
    words = Counter(w[0] for w in people["contributor_first_name"].fillna("").astype(str).str.upper().str.split() if w)
    rare = frozenset(w for w, n in words.items() if n <= 2)
    for _, idx in people.groupby("donor_key").groups.items():
        rows = df.loc[idx]
        lasts = [v.strip() if isinstance(v, str) else "" for v in rows["contributor_last_name"]]
        if not any(lasts):
            continue
        firsts = [v.strip() if isinstance(v, str) else "" for v in rows["contributor_first_name"]]
        surname, given = donor_names(lasts, firsts, rare)
        df.loc[idx, "contributor_last_name"] = surname
        df.loc[idx, "contributor_first_name"] = pd.Series(given, index=idx, dtype=object)
        df.loc[idx, "contributor_name"] = [f"{surname}, {g}" if g else surname for g in given]
