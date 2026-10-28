"""Choose first-name and surname spellings from one donor's filings."""
import re
from collections import Counter
from contextlib import contextmanager

from fec.donor_match.normalize import _NAME_TOKEN_RE

# First names can carry filer decoration that is not name.
_PAREN_GROUP_RE = re.compile(r"\([^()]*\)")


# an unclosed trailing parenthetical: "JOHN (NICK"
_PAREN_OPEN_TAIL_RE = re.compile(r"\([^()]*$")


# trailing spaces, commas or dashes to trim: "SMITH, "
_TRAILING_NOISE_RE = re.compile(r"[\s,\-]+$")


# A period after a whole word: "JOHN." -> "JOHN".
_WORD_PERIOD_RE = re.compile(r"(?<=[A-Z]{3})\.+$")


_COMMA_SUFFIXES = frozenset(
    {"JR", "SR", "II", "III", "IV", "V", "MD", "M.D", "PHD", "PH.D", "ESQ", "DDS", "DO"}
)


# A single-letter initial before the surname: "A LEVY".
_LEADING_INITIAL_RE = re.compile(r"^([A-Z])(\.\s*|\s+)([A-Z][A-Z'\-]+(?:\s.*)?)$")


# check whether a leading letter is really a surname particle
def _is_particle(letter: str, rest: str) -> bool:
    return letter in "OD" or (letter == "L" and rest[:1] in "AEIOUH")


def _first_core(value: str) -> str:
    """Remove brackets, stray punctuation and surname spill-over from first names."""
    text = value
    if "," in text:
        # First names never carry commas; the last part is first.
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


def _only_balanced_parens(value: str) -> bool:
    """True when the spelling's only decoration is complete (...) groups."""
    if "(" not in value:
        return False
    return _first_core(value) == " ".join(_PAREN_GROUP_RE.sub(" ", value).split())


# extract uppercase name tokens from text
def _name_tokens(text: str) -> tuple:
    return tuple(_NAME_TOKEN_RE.findall(text.upper()))


def _parenthesized_tokens(values) -> set:
    """Tokens the donor writes inside brackets: asides, not names."""
    tokens = set()
    for value in values:
        for inner in re.findall(r"\(([^()]*)(?:\)|$)", value):
            tokens.update(_name_tokens(inner))
    return tokens


def _drop_cut_surname(first: str, last_words: set[str], given_names: frozenset) -> str:
    """Drop a truncated surname tail unless it is a name."""
    words = first.split()
    tail = words[-1].upper() if len(words) > 1 else ""
    if (len(tail) >= 3 and tail not in given_names
            and any(word != tail and word.startswith(tail) for word in last_words)):
        return " ".join(words[:-1])
    return first


# True when two words differ by one letter.
def _one_edit_apart(a: str, b: str) -> bool:
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(diffs) == 1 or (
            len(diffs) == 2 and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
        )
    short, long = sorted((a, b), key=len)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


# First names filed at most twice, set per run.
_RARE_SPELLINGS: frozenset[str] = frozenset()


# make the dataset's rare first-name spellings known while choosing names
@contextmanager
def rare_spellings(words):
    """Temporarily set rare first names to separate typos."""
    global _RARE_SPELLINGS
    previous, _RARE_SPELLINGS = _RARE_SPELLINGS, frozenset(words)
    try:
        yield
    finally:
        _RARE_SPELLINGS = previous


def _drop_misspellings(candidates: list[str]) -> list[str]:
    """Drop a rare one-letter variant when the donor prefers another."""
    first_words = []
    for value in candidates:
        words = _first_core(value).split()
        first_words.append(words[0] if words else "")
    firsts = Counter(word for word in first_words if word)
    slips = {
        word for word in firsts
        if len(word) >= 4 and word in _RARE_SPELLINGS and any(
            firsts[other] > firsts[word] and _one_edit_apart(word, other) for other in firsts
        )
    }
    kept = [
        value for value, word in zip(candidates, first_words)
        if word not in slips
    ]
    return kept or candidates


def _choose_first(candidates: list[str]) -> str | None:
    """Choose the fullest real name, then frequency and order."""
    if not candidates:
        return None
    candidates = _drop_misspellings(candidates)
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
    # Fullness counts letters and word breaks, not punctuation.
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


def _surname_parents(variants: list[str], firsts_by_last: dict) -> dict:
    """Map a prefixed surname to its base after an initial."""
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


def _choose_last(lasts: list[str], parents: dict) -> str:
    """Choose the most filed base surname, breaking ties alphabetically."""

    # follow prefixed variants to their root surname
    def _root(value):
        seen = set()
        while value in parents and value not in seen:
            seen.add(value)
            value = parents[value][0]
        return value

    counts = Counter(_root(value) for value in lasts)
    return min(counts, key=lambda v: (-counts[v], v))
