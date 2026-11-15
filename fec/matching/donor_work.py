"""Work fields that need the donor's other filings.

Steps, in the order standardize_donors runs them (fec/matching/pipeline.py):
    1. fill_occupation_from_donor   an empty or SELF-EMPLOYED occupation takes the donor's one role at the employer
    2. fill_status_employer         a status with no employer (RETIRED) is written as the employer too
    3. recategorize_occupations     every category is read again from the occupation as it now stands
    4. settle_retired_employer      a retired filing's employer is RETIRED; its company is the previous employer
    5. categorize_from_donor        an occupation naming no field (BROKER) takes the donor's field at the employer

Steps 1 and 5 work the same way: a "job" is one donor at one employer, and a value is taken from the donor's
other filings of that job only when they all agree on it.
"""

import pandas as pd

from fec.cleaning.filing_corrections import categorize_with_corrections
from fec.cleaning.work.employer_key import employer_text_key
from fec.cleaning.work.employer_status import is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.text import upper_text
from fec.vocabulary.occupations import EMPLOYER_FROM_CATEGORY, FINAL_EMPLOYER_FROM_OCCUPATION
from fec.vocabulary.statuses import SKIP_EMPLOYERS, SKIP_OCCUPATIONS
from fec.vocabulary.tables import words

# Occupations that name no field (BROKER), and the categories that are a rank or a status, not a field.
_FIELDLESS_OCCUPATIONS = words("fieldless_occupations")
_CATEGORIES_WITHOUT_A_FIELD = words("categories_without_a_field")
# One donor at one employer.
_JOB = ["donor_key", "contributor_employer"]


def _find_people_with_an_employer(df: pd.DataFrame) -> pd.DataFrame:
    employer = df["contributor_employer"]
    return df[df["entity_type"].eq("INDIVIDUAL") & employer.notna() & ~employer.isin(SKIP_EMPLOYERS)]


def _take_from_the_job(people: pd.DataFrame, known: pd.Series, vague: pd.Series, column: str) -> pd.Series:
    """For each vague filing, the value its job's known filings all give in this column; none if they differ."""
    values_per_job = people[known].groupby(_JOB)[column].unique()
    agreed = values_per_job[values_per_job.map(len).eq(1)].str[0].to_dict()
    jobs = list(zip(people.loc[vague, _JOB[0]], people.loc[vague, _JOB[1]]))
    return pd.Series(jobs, index=people.index[vague], dtype=object).map(agreed).dropna()


def fill_occupation_from_donor(df: pd.DataFrame) -> None:
    """Fill an empty or SELF-EMPLOYED occupation with the donor's one role at this employer."""
    people = _find_people_with_an_employer(df)
    occupation = people["contributor_occupation"].fillna("").astype(str).str.strip()
    vague = occupation.isin(("", "SELF-EMPLOYED"))
    # A role is a real occupation: not a status, and not the employer's own name.
    names_the_employer = occupation.map(employer_text_key).eq(people["contributor_employer"].map(employer_text_key))
    known = ~vague & ~occupation.isin(SKIP_OCCUPATIONS) & ~names_the_employer
    found = _take_from_the_job(people, known, vague, "contributor_occupation")
    df.loc[found.index, "contributor_occupation"] = found
    df.loc[found.index, "occupation_category"] = categorize_occupations(found)


def fill_status_employer(df: pd.DataFrame) -> None:
    """Write a status with no employer (RETIRED, HOMEMAKER) as the employer too; no company is inferred."""
    status = df["contributor_occupation"].map(FINAL_EMPLOYER_FROM_OCCUPATION)
    status = status.fillna(df["occupation_category"].map(EMPLOYER_FROM_CATEGORY))
    fill = df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].fillna("").eq("") & status.notna()
    df.loc[fill, "contributor_employer"] = status[fill]


def recategorize_occupations(df: pd.DataFrame) -> None:
    """Make every individual's category match the occupation as it now reads."""
    people = df[df["entity_type"].eq("INDIVIDUAL")]
    df.loc[people.index, "occupation_category"] = categorize_with_corrections(people)


def settle_retired_employer(df: pd.DataFrame) -> None:
    """Make a retired filing's employer RETIRED; a company filed there becomes the previous employer."""
    retired_category = df["occupation_category"].eq("RETIRED")
    retired_occupation = upper_text(df["contributor_occupation"]).eq("RETIRED")
    said_retired = retired_category & retired_occupation
    # The filing said RETIRED beside a profession (RETIRED JUDGE MEDIATOR).
    said_retired |= df["_retired_marker"].fillna(False).astype(bool)
    retired = df["entity_type"].eq("INDIVIDUAL") & said_retired & upper_text(df["contributor_employer"]).ne("RETIRED")
    company = retired & upper_text(df["previous_employer"]).eq("") & df["contributor_employer"].map(is_real_employer)
    df.loc[company, "previous_employer"] = df.loc[company, "contributor_employer"]
    df.loc[retired, "contributor_employer"] = "RETIRED"


def categorize_from_donor(df: pd.DataFrame) -> None:
    """Give a field-less occupation (BROKER) the one field the donor's other filings at this employer name."""
    people = _find_people_with_an_employer(df)
    vague = people["contributor_occupation"].isin(_FIELDLESS_OCCUPATIONS)
    known = ~vague & ~people["occupation_category"].isin(_CATEGORIES_WITHOUT_A_FIELD)
    found = _take_from_the_job(people, known, vague, "occupation_category")
    df.loc[found.index, "occupation_category"] = found
