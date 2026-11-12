"""Work fields that need the donor's other filings."""

import pandas as pd

from fec.cleaning.filing_corrections import categorize_with_corrections
from fec.cleaning.work.employer_key import employer_text_key
from fec.cleaning.work.employer_status import is_real_employer
from fec.cleaning.work.occupation_categories import categorize_occupations
from fec.common.text import upper_text
from fec.vocabulary.occupations import EMPLOYER_FROM_CATEGORY, FINAL_EMPLOYER_FROM_OCCUPATION
from fec.vocabulary.statuses import SKIP_EMPLOYERS, SKIP_OCCUPATIONS


def recategorize_occupations(df: pd.DataFrame) -> None:
    """Make every individual's category match the occupation as it now reads."""
    people = df[df["entity_type"].eq("INDIVIDUAL")]
    df.loc[people.index, "occupation_category"] = categorize_with_corrections(people)


def fill_occupation_from_donor(df: pd.DataFrame) -> None:
    """Fill an empty or SELF-EMPLOYED occupation with the donor's one role at this employer."""
    employer = df["contributor_employer"]
    people = df[df["entity_type"].eq("INDIVIDUAL") & employer.notna() & ~employer.isin(SKIP_EMPLOYERS)]
    occupation = people["contributor_occupation"].fillna("").astype(str).str.strip()
    vague = occupation.isin(("", "SELF-EMPLOYED"))
    occupation_key = occupation.map(employer_text_key)
    employer_key = people["contributor_employer"].map(employer_text_key)
    known = people[~vague & ~occupation.isin(SKIP_OCCUPATIONS) & occupation_key.ne(employer_key)]
    job = ["donor_key", "contributor_employer"]
    roles_per_job = known.groupby(job)["contributor_occupation"].unique()
    one_role_jobs = roles_per_job[roles_per_job.map(len).eq(1)]
    roles = one_role_jobs.str[0].to_dict()
    vague_pairs = list(zip(people.loc[vague, job[0]], people.loc[vague, job[1]]))
    vague_jobs = pd.Series(vague_pairs, index=people.index[vague], dtype=object)
    found = vague_jobs.map(roles).dropna()
    df.loc[found.index, "contributor_occupation"] = found
    df.loc[found.index, "occupation_category"] = categorize_occupations(found)


def fill_status_employer(df: pd.DataFrame) -> None:
    """Write a status with no employer (RETIRED, HOMEMAKER) as the employer too; no company is inferred."""
    status = (
        df["contributor_occupation"]
        .map(FINAL_EMPLOYER_FROM_OCCUPATION)
        .fillna(df["occupation_category"].map(EMPLOYER_FROM_CATEGORY))
    )
    fill = df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].fillna("").eq("") & status.notna()
    df.loc[fill, "contributor_employer"] = status[fill]


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
