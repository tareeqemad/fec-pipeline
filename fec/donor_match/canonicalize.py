"""Per-donor name, employer, street, unit, and PO-box canonicalization."""

import re
from collections import Counter, defaultdict

import pandas as pd

from fec.config.constants import EMPLOYER_STATUS_VALUES

from .joint import given_tokens, joint_partners
from .matcher import UnionFind
from .rules import joint_name_exempt
from .scoring import _NAME_TOKEN_RE

# legal suffixes/connectors carry no identity when comparing employer names
_EMP_DROP_TOKENS = frozenset(
    {
        "LLP",
        "LLC",
        "INC",
        "PC",
        "CO",
        "CORP",
        "CORPORATION",
        "COMPANY",
        "LP",
        "LTD",
        "PLLC",
        "PA",
        "APC",
        "CHARTERED",
        "THE",
        "AND",
        "OF",
    }
)
_EMP_TOKEN_RE = re.compile(r"[A-Z0-9]+")

_ADDR_TOKEN_RE = re.compile(r"[A-Z0-9]+")
_POBOX_RE = re.compile(r"\bP\.?\s*O\.?\s*BOX\s*#?\s*(\d+)")

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


def _is_particle(letter: str, rest: str) -> bool:
    return letter in "OD" or (letter == "L" and rest[:1] in "AEIOUH")


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


def _only_balanced_parens(value: str) -> bool:
    """True when the spelling's only decoration is complete (...) groups."""
    if "(" not in value:
        return False
    return _first_core(value) == " ".join(_PAREN_GROUP_RE.sub(" ", value).split())


def _name_tokens(text: str) -> tuple:
    return tuple(_NAME_TOKEN_RE.findall(text.upper()))


def _parenthesized_tokens(values) -> set:
    """Tokens the donor writes inside brackets somewhere: asides, not name parts."""
    tokens = set()
    for value in values:
        for inner in re.findall(r"\(([^()]*)(?:\)|$)", value):
            tokens.update(_name_tokens(inner))
    return tokens


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


def _choose_last(lasts: list[str], parents: dict) -> str:
    """Most filed surname once prefixed variants count toward the surname they end with; a tie still goes to the alphabetically first spelling (Series.mode order)."""

    def _root(value):
        seen = set()
        while value in parents and value not in seen:
            seen.add(value)
            value = parents[value][0]
        return value

    counts = Counter(_root(value) for value in lasts)
    return min(counts, key=lambda v: (-counts[v], v))


def _canonical_person_name(lasts: list[str], firsts: list[str], is_joint=None,
                           given_names: frozenset = frozenset(), by_row: bool = False):
    """(canonical last, canonical first) for one donor from its rows' (last, first) pairs.

    is_joint(first) marks a spelling that also carries a co-filer's given name
    (joint.py). Such a spelling never becomes the donor's name while the donor
    has a solo spelling: the partner's name is not written onto solo filings.

    by_row returns one first name per row instead, unified only among rows
    that carry the same whole given names: a whole extra name may be a middle
    name (JOSE FELIX) or a co-filer who never files alone (SHIRA JARED), and
    the data cannot tell them apart, so a filing never gains or loses one.
    Spelling, nicknames and initials are still unified (MARK -> MARK L.).
    """
    firsts_by_last: dict[str, list[str]] = defaultdict(list)
    for last, first in zip(lasts, firsts):
        if last and first:
            firsts_by_last[last].append(first)
    named = [last for last in lasts if last]
    variants = list(dict.fromkeys(named))
    parents = _surname_parents(variants, firsts_by_last) if len(variants) > 1 else {}
    canon_last = _choose_last(named, parents)

    # one candidate per row: the given name a surname-field prefix supplies, else the first name
    candidates = [parents.get(last, (None, None))[1] or first or None for last, first in zip(lasts, firsts)]

    moved_initial = None
    match = _LEADING_INITIAL_RE.match(canon_last)
    if match and not _is_particle(match.group(1), match.group(3)):
        # filed only as "W. HAHN": the initial is the middle initial
        dot = "." if "." in match.group(2) else ""
        moved_initial = match.group(1) + dot
        canon_last = match.group(3).strip()

    last_words = set(canon_last.upper().split())
    # a name field cut off by FEC's length limit can end in the start of the
    # surname ("GLENN STUART CHRYSTA" for CHRYSTAL): that piece is not a name
    candidates = [
        _drop_cut_surname(first, last_words, given_names) if first else None
        for first in candidates
    ]

    def pick(group: list) -> str | None:
        # candidates must carry a non-surname token: a reversed filing's
        # surname-as-first could otherwise win, then strip to nothing
        fcands = [f for f in group if f and any(w.upper() not in last_words for w in f.split())]
        if is_joint is not None:
            solo = [f for f in fcands if not is_joint(f)]
            if solo:
                fcands = solo
        canon_first = _choose_first(fcands)
        if canon_first:
            kept = [w for w in canon_first.split() if w.upper() not in last_words]
            canon_first = " ".join(kept) or None
        if moved_initial and moved_initial[0] not in _name_tokens(canon_first or ""):
            canon_first = f"{canon_first} {moved_initial}" if canon_first else moved_initial
        return canon_first

    if not by_row:
        return canon_last, pick(candidates)

    own = {word for last in named for word in _name_tokens(last)} | last_words
    own |= _parenthesized_tokens({first for first in candidates if first})
    groups = defaultdict(list)
    for position, first in enumerate(candidates):
        groups[_extra_given_words(first or "", own)].append(position)
    donor_first = pick(candidates)
    if len(groups) == 1:
        return canon_last, [donor_first] * len(candidates)
    by_position = [None] * len(candidates)
    for positions in groups.values():
        group_first = pick([candidates[p] for p in positions]) or donor_first
        for position in positions:
            by_position[position] = group_first
    return canon_last, by_position


def _text_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series("", index=df.index)
    values = df[column]
    return values.where(values.map(lambda v: isinstance(v, str)), "").str.strip().str.upper()


def _household_spellings(df: pd.DataFrame, ind: pd.Series) -> tuple[dict, dict]:
    """donor_key -> its own first-name spellings, and the spellings of the
    OTHER donors with the same surname at one of its streets or ZIPs."""
    keys = df.loc[ind, "donor_key"]
    lasts = _text_column(df, "contributor_last_name")[ind]
    firsts = _text_column(df, "contributor_first_name")[ind]
    streets = _text_column(df, "contributor_street_1")[ind]
    zips = _text_column(df, "contributor_zip")[ind].str[:5]

    own = defaultdict(set)
    spots_of = defaultdict(set)
    at_spot = defaultdict(set)  # (surname, street/zip) -> {(donor_key, spelling)}
    for key, last, first, street, zip5 in zip(keys, lasts, firsts, streets, zips):
        spelling = given_tokens(first)
        if not (isinstance(key, str) and last and spelling):
            continue
        own[key].add(spelling)
        spots = [("S", street)] if street else []
        if len(zip5) == 5:
            spots.append(("Z", zip5))
        for spot in spots:
            spots_of[key].add((last, spot))
            at_spot[(last, spot)].add((key, spelling))

    household = {}
    for key, spots in spots_of.items():
        found = {
            spelling
            for spot in spots
            for other, spelling in at_spot[spot]
            if other != key
        }
        if found:
            household[key] = found
    return own, household


def _shared_given_names(df: pd.DataFrame, ind: pd.Series) -> frozenset:
    """Words at least two donors file in their first-name field: real given names."""
    words = _text_column(df, "contributor_first_name")[ind].str.split()
    donors = pd.DataFrame({"donor": df.loc[ind, "donor_key"], "word": words}).explode("word")
    per_word = donors.dropna().drop_duplicates().groupby("word")["donor"].size()
    return frozenset(per_word[per_word >= 2].index)


def canonicalize_donor_names(df: pd.DataFrame) -> int:
    """Write one canonical last name and each filing's first name, plus a rebuilt LAST, FIRST composite; returns rows changed.

    A spelling that carries a co-filer's name (a joint filing, see joint.py)
    is never chosen for a donor that also files alone under a solo spelling.
    First names are unified only among filings that carry the same whole
    given names (see _canonical_person_name).
    """
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    changed = 0
    fn_col = "contributor_first_name"
    ln_col = "contributor_last_name"
    cn_col = "contributor_name"
    own_spellings, household_spellings = _household_spellings(df, ind)
    given_names = _shared_given_names(df, ind)

    for donor_key, idx in df[ind].groupby("donor_key").groups.items():
        rows = df.loc[idx]
        lasts = [v.strip() if isinstance(v, str) else "" for v in rows[ln_col]]
        if not any(lasts):
            continue
        firsts = [v.strip() if isinstance(v, str) else "" for v in rows[fn_col]]
        is_joint = None
        household = household_spellings.get(donor_key)
        if household:
            own = own_spellings.get(donor_key, set())
            surnames = {last for last in lasts if last}
            verdicts: dict[str, bool] = {}

            def is_joint(first, own=own, household=household, surnames=surnames,
                         verdicts=verdicts):
                if first not in verdicts:
                    verdicts[first] = not any(
                        joint_name_exempt(f"{last}, {first}") for last in surnames
                    ) and bool(joint_partners(given_tokens(first), own, household))
                return verdicts[first]

        canon_last, firsts_by_row = _canonical_person_name(
            lasts, firsts, is_joint, given_names, by_row=True,
        )

        for i, canon_first in zip(idx, firsts_by_row):
            # composite rebuilt from the canonical pair, in FEC's "LAST, FIRST" form
            canon_name = f"{canon_last}, {canon_first}" if canon_first else canon_last
            cur_f = df.at[i, fn_col]
            cur_l = df.at[i, ln_col]
            cf = cur_f if (isinstance(cur_f, str) and cur_f.strip()) else None
            cl = cur_l if (isinstance(cur_l, str) and cur_l.strip()) else None
            cur_n = df.at[i, cn_col]
            if cf != canon_first or cl != canon_last or cur_n != canon_name:
                df.at[i, fn_col] = canon_first
                df.at[i, ln_col] = canon_last
                df.at[i, cn_col] = canon_name
                changed += 1
    return changed


def _extra_given_words(first: str, own: set) -> frozenset:
    """The whole given names after the first name, the donor's own words aside."""
    words = first.split()
    if not words or len(_name_tokens(words[0])) != 1 or len(_name_tokens(words[0])[0]) == 1:
        return frozenset()  # empty, bracketed, or an initial: the next word is the name
    return frozenset(
        token for word in words[1:] for token in _name_tokens(word)
        if len(token) > 1 and token not in own
    )

def _emp_core_tokens(name: str) -> frozenset:
    """Significant tokens of an employer name (legal suffixes/connectors removed)."""
    toks = _EMP_TOKEN_RE.findall(name.upper())
    return frozenset(t for t in toks if t not in _EMP_DROP_TOKENS and len(t) > 1)


def _employer_variant_map(names: list[str]) -> dict[str, str]:
    cores = {name: _emp_core_tokens(name) for name in names}
    union = UnionFind()
    for left_index in range(len(names)):
        for right_index in range(left_index + 1, len(names)):
            left = names[left_index]
            right = names[right_index]
            left_core = cores[left]
            right_core = cores[right]
            if len(left_core & right_core) >= 2 and (
                left_core <= right_core or right_core <= left_core
            ):
                union.union(left, right)

    clusters = defaultdict(list)
    for name in names:
        clusters[union.find(name)].append(name)

    remap = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        canonical = max(members, key=lambda name: (len(cores[name]), len(name)))
        remap.update({name: canonical for name in members if name != canonical})
    return remap


def _apply_employer_variants(df: pd.DataFrame, indexes, remap: dict) -> int:
    changed = 0
    for index in indexes:
        current = df.at[index, "contributor_employer"]
        if isinstance(current, str) and current.strip() in remap:
            df.at[index, "contributor_employer"] = remap[current.strip()]
            changed += 1
    return changed


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    """Unify clear employer variants within each donor's history."""
    individuals = df["entity_type"] == "INDIVIDUAL"
    if not individuals.any():
        return 0

    changed = 0
    for indexes in df[individuals].groupby("donor_key").groups.values():
        values = df.loc[indexes, "contributor_employer"].dropna().map(str).str.strip()
        names = [
            name
            for name in values.unique()
            if name and name.upper() not in EMPLOYER_STATUS_VALUES
        ]
        if len(names) >= 2:
            remap = _employer_variant_map(names)
            if remap:
                changed += _apply_employer_variants(df, indexes, remap)
    return changed


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """Rename ORGANIZATION donors to the canonical employer spelling of the same company (reuses canonical_key, adds no new normalization); returns rows aligned."""
    from fec.cleaning.employer_synonyms import canonical_key

    # canonical display name per canonical_key = the donor-side spelling seen most
    ind = df[df["entity_type"] == "INDIVIDUAL"]
    emp = ind["contributor_employer"].dropna().astype(str).str.strip()
    emp = emp[(emp != "") & (~emp.str.upper().isin(EMPLOYER_STATUS_VALUES))]
    if emp.empty:
        return 0
    by_key: dict[str, str] = {}
    for name in emp.value_counts().index:  # value_counts: most common first
        k = canonical_key(name)
        if k and k not in by_key:
            by_key[k] = name

    def _forms(n: str):
        out = [n]
        if "," in n:  # "CAPITAL, WHITE" -> "WHITE CAPITAL"
            a, b = n.split(",", 1)
            out.append(f"{b.strip()} {a.strip()}")
        return out

    org_names = (
        df.loc[df["entity_type"] == "ORGANIZATION", "contributor_name"]
        .dropna()
        .unique()
    )
    remap: dict[str, str] = {}
    for nm in org_names:
        for form in _forms(str(nm)):
            k = canonical_key(form)
            if k and k in by_key and by_key[k].upper() != str(nm).upper():
                remap[nm] = by_key[k]
                break
    if not remap:
        return 0
    mask = (df["entity_type"] == "ORGANIZATION") & df["contributor_name"].isin(remap)
    df.loc[mask, "contributor_name"] = df.loc[mask, "contributor_name"].map(remap)
    return int(mask.sum())


# trailing legal form of a company name ("EATON STEEL CORPORATION" -> "EATON STEEL")
_ORG_LEGAL_TAIL_RE = re.compile(
    r"(?:[\s,]+(?:CORPORATION|CORP|INCORPORATED|INC|COMPANY|CO|LLC|LLP|LTD|LP|PLLC)\.?)+$"
)


def unify_org_donor_suffix_variants(df: pd.DataFrame) -> int:
    """ORGANIZATION donors whose name is another organization donor's name plus a trailing legal form (EATON STEEL CORPORATION next to EATON STEEL) take the suffix-free spelling; names only, donor_keys are untouched; returns rows renamed."""
    org = df["entity_type"] == "ORGANIZATION"
    if not org.any():
        return 0
    names = df.loc[org, "contributor_name"].dropna().astype(str).str.strip()
    distinct = set(names[names != ""])
    remap = {}
    for name in distinct:
        bare = _ORG_LEGAL_TAIL_RE.sub("", name).strip()
        # the bare spelling must itself be filed as an organization donor:
        # only then is it provably the same name, not a guessed short form
        if bare and bare != name and bare in distinct:
            remap[name] = bare
    if not remap:
        return 0
    current = df["contributor_name"].where(df["contributor_name"].notna(), "").astype(str).str.strip()
    mask = org & current.isin(remap)
    df.loc[mask, "contributor_name"] = current[mask].map(remap)
    return int(mask.sum())


def _addr_fingerprint(street: str) -> str:
    """Order-independent street key: house number anchored, remaining tokens sorted (keeps grid addresses distinct)."""
    toks = _ADDR_TOKEN_RE.findall(street.upper())
    if not toks:
        return ""
    return toks[0] + "|" + " ".join(sorted(toks[1:]))


def canonicalize_donor_addresses(df: pd.DataFrame) -> int:
    """Collapse per-donor street_1 variants with the same ZIP and anchored token set to the most common form; returns rows rewritten."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0

    st_col = "contributor_street_1"
    zip_col = "contributor_zip"
    changed = 0

    for idx in df[ind].groupby("donor_key").groups.values():
        # bucket this donor's rows by (zip, anchored-fingerprint)
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s = df.at[i, st_col]
            if not (isinstance(s, str) and s.strip()):
                continue
            z = df.at[i, zip_col]
            z = z if isinstance(z, str) else ""
            fp = _addr_fingerprint(s)
            if fp:
                buckets[(z, fp)].append(i)

        for rows in buckets.values():
            forms = [df.at[i, st_col].strip() for i in rows]
            distinct = set(forms)
            if len(distinct) < 2:
                continue
            # canonical = most common spelling (tie: deterministic first)
            canon = max(sorted(distinct), key=lambda f: forms.count(f))
            for i, f in zip(rows, forms):
                if f != canon:
                    df.at[i, st_col] = canon
                    changed += 1
    return changed


def canonicalize_donor_units(df: pd.DataFrame) -> int:
    """Collapse per-donor street_2 spellings of the same unit (APT/UNIT/# 1503), bucketed by (street_1, ZIP, unit id), to the dominant form; returns rows rewritten."""
    ind = df["entity_type"] == "INDIVIDUAL"
    if not ind.any():
        return 0
    from fec.cleaning.pipeline.address_fixes import _unit_core

    st1_col = "contributor_street_1"
    st2_col = "contributor_street_2"
    zip_col = "contributor_zip"
    changed = 0

    for idx in df[ind].groupby("donor_key").groups.values():
        buckets: dict[tuple, list] = defaultdict(list)
        for i in idx:
            s2 = df.at[i, st2_col]
            if not (isinstance(s2, str) and s2.strip()):
                continue
            core = _unit_core(s2)
            if not core:
                continue
            s1 = df.at[i, st1_col]
            s1 = s1.strip() if isinstance(s1, str) else ""
            z = df.at[i, zip_col]
            z = z if isinstance(z, str) else ""
            buckets[(s1, z, core)].append(i)

        for rows in buckets.values():
            forms = [df.at[i, st2_col].strip() for i in rows]
            if len(set(forms)) < 2:
                continue
            canon = max(sorted(set(forms)), key=lambda f: (forms.count(f), len(f)))
            for i, f in zip(rows, forms):
                if f != canon:
                    df.at[i, st2_col] = canon
                    changed += 1
    return changed


def _pobox_num(street: str) -> str:
    """Extract the box number from a PO-box street, else ''."""
    m = _POBOX_RE.search(street.upper())
    return m.group(1) if m else ""


def _is_insertion_typo(a: str, b: str) -> bool:
    """True iff one string is the other with exactly one extra digit inserted; same-length pairs never match."""
    if abs(len(a) - len(b)) != 1:
        return False
    short, long = (a, b) if len(a) < len(b) else (b, a)
    i = 0
    for ch in long:
        if i < len(short) and ch == short[i]:
            i += 1
    return i == len(short)


def _pobox_rows_by_zip(df: pd.DataFrame, indexes) -> dict[str, list]:
    by_zip = defaultdict(list)
    for index in indexes:
        street = df.at[index, "contributor_street_1"]
        box = _pobox_num(street) if isinstance(street, str) else ""
        if not box:
            continue
        zip_code = df.at[index, "contributor_zip"]
        zip_code = zip_code if isinstance(zip_code, str) else ""
        by_zip[zip_code].append((index, street.strip(), box))
    return by_zip


def _pobox_clusters(numbers: list[str]) -> list[list[str]]:
    union = UnionFind()
    for left in numbers:
        for right in numbers:
            if left < right and _is_insertion_typo(left, right):
                union.union(left, right)

    clusters = defaultdict(list)
    for number in numbers:
        clusters[union.find(number)].append(number)
    return list(clusters.values())


def _apply_pobox_cluster(df: pd.DataFrame, rows: list, members: list[str]) -> int:
    frequencies = defaultdict(int)
    for _, _, box in rows:
        frequencies[box] += 1
    canonical_box = max(
        members,
        key=lambda box: (frequencies[box], -len(box)),
    )
    canonical_forms = [street for _, street, box in rows if box == canonical_box]
    canonical = max(set(canonical_forms), key=canonical_forms.count)

    changed = 0
    for index, street, box in rows:
        if box in members and street != canonical:
            df.at[index, "contributor_street_1"] = canonical
            changed += 1
    return changed


def canonicalize_donor_pobox_typos(df: pd.DataFrame) -> int:
    """Unify one-digit insertion typos in a donor's same-ZIP PO boxes."""
    changed = 0
    for indexes in df.groupby("donor_key").groups.values():
        for rows in _pobox_rows_by_zip(df, indexes).values():
            numbers = sorted({box for _, _, box in rows})
            if len(numbers) < 2:
                continue
            for members in _pobox_clusters(numbers):
                if len(members) >= 2:
                    changed += _apply_pobox_cluster(df, rows, members)
    return changed
