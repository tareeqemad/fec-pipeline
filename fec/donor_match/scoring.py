"""Score the evidence shared by two donor profiles."""

from typing import NamedTuple

from rapidfuzz.distance import Levenshtein

from fec.donor_match.normalize import name_words

# Generic categories support no match; retirement is scored separately.
GENERIC_OCC_CATEGORIES = {
    "RETIRED",
    "NOT EMPLOYED",
    "SELF-EMPLOYED",
    "OTHER",
    "ORGANIZATION",
    "POLITICAL COMMITTEE",
    "HOMEMAKER",
    "STUDENT",
    "NAN",
    "",
}

MERGE_THRESHOLD = 50  # a pair at this score is one donor, unless a rule separates it
BLOCKED = -999  # two different full middle names: different people

# shared evidence
SCORE_SAME_STREET = 60
SCORE_SAME_EMPLOYER = 30
SCORE_SAME_STATE = 10
SCORE_SAME_CITY = 15
SCORE_SAME_ZIP5 = 25
SCORE_SAME_ZIP3 = 5  # same ZIP area (first 3 digits)
SCORE_SAME_OCC = 15  # same occupation category
SCORE_OCC_RETIRED = 10  # an occupation on one record, retired on the other
SCORE_MIDDLE_MATCH = 15  # same middle, an initial of the full form, or a one-letter slip
SCORE_MIDDLE_PARTIAL = 5  # only one side has a middle name
SCORE_MIDDLE_CONFLICT = -30  # different initials
# name rarity (name_freq = profiles sharing the normalised name)
SCORE_RARE_NAME = 15  # freq <= 3
SCORE_VERY_RARE_BONUS = 20  # freq <= 2, on top of the rare bonus
SCORE_RETIRED_NO_EMP = 25  # no employer on either side, a rare name and shared geography
SCORE_RARE_EMPLOYER_OK = 10  # no geography, but a shared employer and a rare name
SCORE_CROSS_NAME_BONUS = 10  # another spelling of the name, only with corroboration
EMPLOYER_ONLY_COMMON_CAP = 30  # a common name sharing only an employer never merges


class Evidence(NamedTuple):
    """Hold a pair's match score and which shared facts support it."""

    score: int
    street: bool = False
    zip5: bool = False
    employer: bool = False
    middle_conflict: bool = False

    @property
    def anchored(self) -> bool:
        """A shared street, ZIP or employer stands behind the score."""
        return self.street or self.zip5 or self.employer


def _middle_score(m1: str, m2: str) -> int:
    """Points for the middle names; BLOCKED for two different full names."""
    if not m1 and not m2:
        return 0
    if not m1 or not m2:
        return SCORE_MIDDLE_PARTIAL
    if m1 == m2 or (len(m1) == 1 and m2[0] == m1) or (len(m2) == 1 and m1[0] == m2):
        return SCORE_MIDDLE_MATCH
    if len(m1) == 1 and len(m2) == 1:
        return SCORE_MIDDLE_CONFLICT
    return SCORE_MIDDLE_MATCH if Levenshtein.distance(m1, m2) == 1 else BLOCKED


def compare(p1: dict, p2: dict, name_freq: int) -> Evidence:
    """Return the match score of two profiles and the anchors behind it.

    Two spellings of the same words (LAST, FIRST MIDDLE / LAST, MIDDLE
    FIRST) name one person, so their middle names are not compared.
    """
    street = bool(p1["streets"] & p2["streets"])
    employer = bool(p1["norm_employers"] & p2["norm_employers"])
    state = bool(p1["state"]) and p1["state"] == p2["state"]
    z1, z2 = p1["zip5"], p2["zip5"]
    zips = len(z1) == 5 and len(z2) == 5
    zip5 = zips and z1 == z2
    zip3 = zips and not zip5 and z1[:3] == z2[:3]
    has_geo = street or state or zip5 or zip3
    score = (
        street * SCORE_SAME_STREET
        + employer * SCORE_SAME_EMPLOYER
        + state * SCORE_SAME_STATE
        + (bool(p1["city"]) and p1["city"] == p2["city"]) * SCORE_SAME_CITY
        + zip5 * SCORE_SAME_ZIP5
        + zip3 * SCORE_SAME_ZIP3
    )

    c1, c2 = p1["occ_categories"], p2["occ_categories"]
    if c1 & c2:
        score += SCORE_SAME_OCC
    elif (c1 and p2["retired"]) or (c2 and p1["retired"]):
        score += SCORE_OCC_RETIRED

    middle = 0
    if name_words(p1["name"]) != name_words(p2["name"]):
        middle = _middle_score(p1["middle"], p2["middle"])
        if middle == BLOCKED:
            return Evidence(BLOCKED, street, zip5, employer)
        score += middle

    if name_freq <= 3:
        score += SCORE_RARE_NAME
    if name_freq <= 2:
        score += SCORE_VERY_RARE_BONUS
    if has_geo and name_freq <= 3 and not p1["norm_employers"] and not p2["norm_employers"]:
        score += SCORE_RETIRED_NO_EMP

    # Without geography only a shared employer of a rare name is enough.
    if not has_geo:
        if employer and name_freq <= 3:
            score += SCORE_RARE_EMPLOYER_OK
        elif employer and name_freq > 10:
            score = min(score, EMPLOYER_ONLY_COMMON_CAP)
        else:
            score = min(score, MERGE_THRESHOLD - 1)
    return Evidence(score, street, zip5, employer, middle == SCORE_MIDDLE_CONFLICT)
