"""Score the evidence shared by two donor profiles."""

from typing import NamedTuple

from rapidfuzz.distance import Levenshtein

from fec.matching.name_compare import filed_name_words

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
    anchored: bool  # a shared street, ZIP or employer stands behind the score
    middle_conflict: bool = False


def _score_middle_names(middle_a: str, middle_b: str) -> int:
    if not middle_a and not middle_b:
        return 0
    if not middle_a or not middle_b:
        return SCORE_MIDDLE_PARTIAL
    if (
        middle_a == middle_b
        or (len(middle_a) == 1 and middle_b[0] == middle_a)
        or (len(middle_b) == 1 and middle_a[0] == middle_b)
    ):
        return SCORE_MIDDLE_MATCH
    if len(middle_a) == 1 and len(middle_b) == 1:
        return SCORE_MIDDLE_CONFLICT
    return SCORE_MIDDLE_MATCH if Levenshtein.distance(middle_a, middle_b) == 1 else BLOCKED


def _limit_without_geography(score: int, employer: bool, name_freq: int) -> int:
    if employer and name_freq <= 3:
        return score + SCORE_RARE_EMPLOYER_OK
    if employer and name_freq > 10:
        return min(score, EMPLOYER_ONLY_COMMON_CAP)
    return min(score, MERGE_THRESHOLD - 1)


def compare_profiles(profile_a: dict, profile_b: dict, name_freq: int) -> Evidence:
    """Return the match score of two profiles and the anchors behind it."""
    street = bool(profile_a["streets"] & profile_b["streets"])
    employer = bool(profile_a["norm_employers"] & profile_b["norm_employers"])
    state = bool(profile_a["state"]) and profile_a["state"] == profile_b["state"]
    city = bool(profile_a["city"]) and profile_a["city"] == profile_b["city"]
    zip_a, zip_b = profile_a["zip5"], profile_b["zip5"]
    both_zips = len(zip_a) == 5 and len(zip_b) == 5
    zip5 = both_zips and zip_a == zip_b
    zip3 = both_zips and not zip5 and zip_a[:3] == zip_b[:3]
    has_geo = street or state or zip5 or zip3
    anchored = street or zip5 or employer
    score = (
        street * SCORE_SAME_STREET
        + employer * SCORE_SAME_EMPLOYER
        + state * SCORE_SAME_STATE
        + city * SCORE_SAME_CITY
        + zip5 * SCORE_SAME_ZIP5
        + zip3 * SCORE_SAME_ZIP3
    )

    categories_a, categories_b = profile_a["occ_categories"], profile_b["occ_categories"]
    if categories_a & categories_b:
        score += SCORE_SAME_OCC
    elif (categories_a and profile_b["retired"]) or (categories_b and profile_a["retired"]):
        score += SCORE_OCC_RETIRED

    middle = 0
    # The same words in another order (LAST, MIDDLE FIRST) name one person: their middle names are not compared.
    if filed_name_words(profile_a["name"]) != filed_name_words(profile_b["name"]):
        middle = _score_middle_names(profile_a["middle"], profile_b["middle"])
        if middle == BLOCKED:
            return Evidence(BLOCKED, anchored)
        score += middle

    if name_freq <= 3:
        score += SCORE_RARE_NAME
    if name_freq <= 2:
        score += SCORE_VERY_RARE_BONUS
    no_employers = not profile_a["norm_employers"] and not profile_b["norm_employers"]
    if has_geo and name_freq <= 3 and no_employers:
        score += SCORE_RETIRED_NO_EMP

    if not has_geo:
        score = _limit_without_geography(score, employer, name_freq)
    return Evidence(score, anchored, middle == SCORE_MIDDLE_CONFLICT)
