"""Occupation fixes that need donor_key."""
import pandas as pd

from fec.cleaning.work.employer_canonical import employer_text_key
from fec.cleaning.rules.manual import categorize_records
from fec.cleaning.work.normalize import categorize_final
from fec.config.constants import SKIP_EMPLOYERS, SKIP_OCCUPATIONS


# sync occupation_category to match each individual's final occupation
def _rederive_occupation_category(df: pd.DataFrame) -> int:
    """AT. Make every individual's category match their final occupation."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    expected = categorize_records(df.loc[is_indiv])
    current = df.loc[is_indiv, 'occupation_category'].fillna('')
    changed = current.ne(expected)

    df.loc[expected.index[changed], 'occupation_category'] = expected.loc[changed]
    return int(changed.sum())


# Empty or SELF-EMPLOYED takes the donor's one role at this employer.
def _fill_occupation_from_donor(df: pd.DataFrame) -> int:
    employer = df['contributor_employer']
    people = df[df['entity_type'].eq('INDIVIDUAL') & employer.notna() & ~employer.isin(SKIP_EMPLOYERS)]
    occupation = people['contributor_occupation'].fillna('').astype(str).str.strip()
    vague = occupation.isin(('', 'SELF-EMPLOYED'))

    known = people[
        ~vague & ~occupation.isin(SKIP_OCCUPATIONS)
        & occupation.map(employer_text_key).ne(people['contributor_employer'].map(employer_text_key))
    ]
    jobs = ['donor_key', 'contributor_employer']
    roles = known.groupby(jobs)['contributor_occupation'].unique()
    roles = roles[roles.map(len).eq(1)].str[0].to_dict()

    targets = people[vague]
    found = pd.Series(
        [roles.get(job) for job in zip(targets['donor_key'], targets['contributor_employer'])],
        index=targets.index, dtype=object,
    ).dropna()
    df.loc[found.index, 'contributor_occupation'] = found
    df.loc[found.index, 'occupation_category'] = categorize_final(found)
    return len(found)
