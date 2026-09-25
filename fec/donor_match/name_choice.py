"""Choose a donor's first and last name from the spellings it files."""
import re
from collections import Counter

from fec.donor_match.scoring import _NAME_TOKEN_RE

# --- canonical person names -------------------------------------------------
# A first-name spelling can carry filer decoration that is not part of the
# name: a parenthetical (nickname, spouse, initials), a stray or unclosed
# bracket, a trailing dash/comma, or a period after a whole word ("JOHN.").
# A period after an initial or a two-letter abbreviation ("L.", "JR.") is
# ordinary spelling and stays.
_PAREN_GROUP_RE = re.compile(r"\([^()]*\)")


_PAREN_OPEN_TAIL_RE = re.compile(r"\([^()]*$")


_TRAILING_NOISE_RE = re.compile(r"[\s,\-]+$")


_WORD_PERIOD_RE = re.compile(r"(?<=[A-Z]{3})\.+$")


_COMMA_SUFFIXES = frozenset(
    {"JR", "SR", "II", "III", "IV", "V", "MD", "M.D", "PHD", "PH.D", "ESQ", "DDS", "DO"}
)


# "A LEVY", "W. HAHN", "B.POLLACK": a single-letter initial in front of the
# surname. A surname filed only that way keeps a letter that can be a
# particle with its apostrophe dropped: O (O BRIEN), D (D ANGELO, D SOUZA),
# and L before a vowel or H (L ESPERANCE, L HEUREUX).
_LEADING_INITIAL_RE = re.compile(r"^([A-Z])(\.\s*|\s+)([A-Z][A-Z'\-]+(?:\s.*)?)$")


# check whether a leading letter is really a surname particle
def _is_particle(letter: str, rest: str) -> bool:
    return letter in "OD" or (letter == "L" and rest[:1] in "AEIOUH")


# strip filer decoration from a first-name spelling
def _first_core(value: str) -> str:
    """The first-name spelling with filer decoration removed (see above)."""
    text = value
    if "," in text:
        # "AM, DANIEL": a first name never carries a comma. As in the cleaner's
        # own "LAST, X, FIRST" rule, the part after the last comma is the first
        # name and the rest is spill-over ("HEY,AM"); a bare suffix after a
        # comma ("JAMES, JR") is not a first name
        parts = [part.strip() for part in text.split(",") if part.strip()]
        named = [p for p in parts if p.upper().rstrip(".") not in _COMMA_SUFFIXES]
        text = (named or parts or [""])[-1]
    text = _PAREN_GROUP_RE.sub(" ", text)
    text = _PAREN_OPEN_TAIL_RE.sub(" ", text)
    text = " ".join(text.replace(")", " ").split())
    text = _TRAILING_NOISE_RE.sub("", text)
    text = _WORD_PERIOD_RE.sub("", text)
    text = _TRAILING_NOISE_RE.sub("", text)
    if not text:  # "(JIM)" alone: the bracket content is all there is
        text = " ".join(re.sub(r"[(),]", " ", value).split())
    return text


# check whether decoration is only complete parenthetical groups
def _only_balanced_parens(value: str) -> bool:
    """True when the spelling's only decoration is complete (...) groups."""
    if "(" not in value:
        return False
    return _first_core(value) == " ".join(_PAREN_GROUP_RE.sub(" ", value).split())


# extract uppercase name tokens from text
def _name_tokens(text: str) -> tuple:
    return tuple(_NAME_TOKEN_RE.findall(text.upper()))


# collect tokens the donor writes inside brackets across spellings
def _parenthesized_tokens(values) -> set:
    """Tokens the donor writes inside brackets somewhere: asides, not name parts."""
    tokens = set()
    for value in values:
        for inner in re.findall(r"\(([^()]*)(?:\)|$)", value):
            tokens.update(_name_tokens(inner))
    return tokens


# drop a trailing word that is really a cut surname
def _drop_cut_surname(first: str, last_words: set[str], given_names: frozenset) -> str:
    """'GLENN STUART CHRYSTA' -> 'GLENN STUART' when the surname is CHRYSTAL.

    Only a piece no other donor files as a given name: a middle name JOHN next
    to the surname JOHNSON is a real name and stays.
    """
    words = first.split()
    tail = words[-1].upper() if len(words) > 1 else ""
    if (len(tail) >= 3 and tail not in given_names
            and any(word != tail and word.startswith(tail) for word in last_words)):
        return " ".join(words[:-1])
    return first


# pick the fullest, most-filed first name spelling
def _choose_first(candidates: list[str]) -> str | None:
    """Fullest first name by its real letters, spelled the way the donor files it.

    Decoration is ignored when measuring the name: brackets and their content
    (a nickname, a spouse, initials), a trailing dash/comma, a period after a
    whole word or an initial, and any later word the donor puts in brackets in
    another filing ("LYON LENNY" next to "LYON (LENNY)"). The fullest name
    still wins, so "MARK L." beats "MARK". When different names are equally
    full ("ADAM" / "ADDM", "MARC L" / "MARC I") the one the donor files most
    often wins, and only a tie falls back to the first filed; among the
    undecorated spellings of the chosen name the longest wins ("MARK L." over
    "MARK L"), then the most filed, then the first filed. A balanced
    parenthetical ("JAMES (JIM)") is kept only when the donor never filed the
    name without decoration; a spelling that is only ever broken ("ANNA)",
    "MIRIAM.") is shown without the decoration.
    """
    if not candidates:
        return None
    counts = Counter(candidates)
    order: dict[str, int] = {}
    for position, value in enumerate(candidates):
        order.setdefault(value, position)
    asides = _parenthesized_tokens(order)

    # strip decoration ignored when measuring a name's fullness
    def _core(value):
        words = _first_core(value).split()
        kept = words[:1] + [
            word for word in words[1:]
            if not (_name_tokens(word) and set(_name_tokens(word)) <= asides)
        ]
        return " ".join(kept)

    cores = {value: _core(value) for value in order}
    # fullness = the name's letters and word breaks, not its punctuation: the
    # period of "ISAAC S." must not outweigh the "A" of "ISAAC A"
    fullness = {value: len(" ".join(_name_tokens(cores[value]))) for value in order}
    longest = max(fullness.values())
    filings: Counter = Counter()
    first_filed: dict[tuple, int] = {}
    for value in order:
        if fullness[value] == longest:
            key = _name_tokens(cores[value])
            filings[key] += counts[value]
            first_filed.setdefault(key, order[value])
    group_key = min(filings, key=lambda key: (-filings[key], first_filed[key]))
    group = [value for value in order if _name_tokens(cores[value]) == group_key]

    clean = [value for value in group if cores[value] == value]
    if clean:  # longest spelling, then the most filed, then the first filed
        return min(clean, key=lambda v: (-len(v), -counts[v], order[v]))
    ranked = sorted(group, key=lambda v: (-counts[v], -len(v), order[v]))
    for value in ranked:
        if _only_balanced_parens(value) and _first_core(value) == cores[value]:
            return value
    return cores[ranked[0]]


# map a surname variant to its shorter surname
def _surname_parents(variants: list[str], firsts_by_last: dict) -> dict:
    """variant -> (shorter surname of the same donor it ends with, given name or None).

    "A LEVY" / "B.POLLACK" next to the donor's own "LEVY" / "POLLACK": a
    middle initial typed into the surname field. "EVAN ROSEN" next to
    "ROSEN, EVAN", "DIANE R. TISHKOFF" next to "TISHKOFF, DIANE", "LEITMAN
    BAILEY" next to "BAILEY, ADAM LEITMAN": every word in front is one the
    donor files as a given name (or an initial). When the text in front starts
    with the donor's first name it is returned as that row's given name: such
    a row's first-name field holds initials or a co-filer ("ER", "ROCHEL
    GROSZ"). Any other text in front (HARMATZ SANDERS) may be a real double
    surname and is left alone.
    """
    parents = {}
    for variant in variants:
        given_tokens, lead_tokens = set(), set()
        for last, firsts in firsts_by_last.items():
            if last != variant:
                for first in firsts:
                    tokens = _name_tokens(first)
                    given_tokens.update(tokens)
                    lead_tokens.update(tokens[:1])
        for base in sorted(variants, key=len, reverse=True):
            if base == variant or len(base) < 2 or not variant.endswith(base):
                continue
            head = variant[: -len(base)]
            if not head or head[-1] not in " .":
                continue
            prefix = head.strip(" ")
            tokens = _name_tokens(prefix)
            if not tokens:
                continue
            if len(tokens) == 1 and len(tokens[0]) == 1:
                parents[variant] = (base, None)
                break
            if any(len(t) > 1 for t in tokens) and all(
                len(t) == 1 or t in given_tokens for t in tokens
            ):
                parents[variant] = (base, prefix if tokens[0] in lead_tokens else None)
                break
    return parents


# pick the most filed surname, folding prefixed variants together
def _choose_last(lasts: list[str], parents: dict) -> str:
    """Most filed surname once prefixed variants count toward the surname they end with; a tie still goes to the alphabetically first spelling (Series.mode order)."""

    # follow prefixed variants to their root surname
    def _root(value):
        seen = set()
        while value in parents and value not in seen:
            seen.add(value)
            value = parents[value][0]
        return value

    counts = Counter(_root(value) for value in lasts)
    return min(counts, key=lambda v: (-counts[v], v))
