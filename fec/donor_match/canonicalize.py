"""Per-donor name, employer, street, unit, and PO-box canonicalization."""

from collections import defaultdict

import pandas as pd

from fec.donor_match.constants import NICKNAME_MAP
from fec.donor_match.joint import given_tokens, joint_partners
from fec.donor_match.name_choice import (
    _LEADING_INITIAL_RE,
    _choose_first,
    _choose_last,
    _drop_cut_surname,
    _is_particle,
    _name_tokens,
    _parenthesized_tokens,
    _surname_parents,
)
from fec.donor_match.rules import joint_name_exempt


# pick a donor's canonical last name and first name(s)
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
    Spelling, nicknames and initials are still unified (MARK -> MARK L.,
    M STEPHEN -> MARVIN STEPHEN).
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

    # pick the canonical first name for one candidate group
    def pick(group: list) -> str | None:
        return _pick_first(group, last_words, is_joint, moved_initial)

    if not by_row:
        return canon_last, pick(candidates)
    own = {word for last in named for word in _name_tokens(last)} | last_words
    return canon_last, _firsts_by_row(candidates, own, pick)


# the canonical first name among one group's candidates
def _pick_first(group: list, last_words: set, is_joint, moved_initial: str | None) -> str | None:
    """The canonical first name among one group's candidates."""
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


# one first name per row, unified within matching given-name groups
def _firsts_by_row(candidates: list, own: set, pick) -> list:
    """One first name per row, unified only among rows with the same whole given names."""
    own = own | _parenthesized_tokens({first for first in candidates if first})
    groups = defaultdict(list)
    for position, first in enumerate(candidates):
        groups[_extra_given_words(first or "", own)].append(position)
    groups = _join_initial_groups(groups, candidates)
    donor_first = pick(candidates)
    if len(groups) == 1:
        return [donor_first] * len(candidates)
    by_position = [None] * len(candidates)
    for positions in groups.values():
        group_first = pick([candidates[p] for p in positions]) or donor_first
        for position in positions:
            by_position[position] = group_first
    return by_position


# a dataframe column as trimmed, uppercased strings, '' for non-strings
def _text_column(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series("", index=df.index)
    values = df[column]
    return values.where(values.map(lambda v: isinstance(v, str)), "").str.strip().str.upper()


# each donor's own and same-household first-name spellings
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


# words at least two donors file as a first name
def _shared_given_names(df: pd.DataFrame, ind: pd.Series) -> frozenset:
    """Words at least two donors file in their first-name field: real given names."""
    words = _text_column(df, "contributor_first_name")[ind].str.split()
    donors = pd.DataFrame({"donor": df.loc[ind, "donor_key"], "word": words}).explode("word")
    per_word = donors.dropna().drop_duplicates().groupby("word")["donor"].size()
    return frozenset(per_word[per_word >= 2].index)


# write each donor's canonical name across all its filings
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

            # true if this spelling names a co-filer, cached per spelling
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


# merge a group into a fuller one its initials spell
def _join_initial_groups(groups: dict, candidates: list) -> dict:
    """Join a group to a fuller one whose extra names its middle initials spell.

    "FRANKLIN J." joins "FRANKLIN J. JAY": the one name it lacks starts with
    its middle initial. "SHIRA" never joins "SHIRA JARED".
    """
    initials = {
        key: {token for p in positions for token in _name_tokens(candidates[p] or "")[1:] if len(token) == 1}
        for key, positions in groups.items()
    }
    joined = defaultdict(list)
    for key, positions in groups.items():
        first, names = key
        fuller = [
            other for other in groups
            if other[0] == first and names < other[1]
            and all(name[0] in initials[key] for name in other[1] - names)
        ]
        target = max(fuller, key=lambda other: (len(groups[other]), sorted(other[1]))) if fuller else key
        joined[target].extend(positions)
    return joined


# key grouping a first name by nickname and extra words
def _extra_given_words(first: str, own: set) -> tuple:
    """(initial of the first name, the whole names after it), the donor's own words aside.

    MARTY and MARTIN, BILL and WILLIAM, P. HOWARD and P.HOWARD, M STEPHEN and MARVIN STEPHEN
    share a key and are unified; MARVIN and M STEPHEN, SHIRA and SHIRA JARED
    do not.
    """
    tokens = _name_tokens(first)
    if not tokens:
        return ("", frozenset())
    return (
        NICKNAME_MAP.get(tokens[0], tokens[0])[0],  # BILL keys as WILLIAM
        frozenset(token for token in tokens[1:] if len(token) > 1 and token not in own),
    )


