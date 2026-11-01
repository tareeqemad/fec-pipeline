"""Move a retiree's filed company to previous_employer."""
import pandas as pd

from fec.cleaning._helpers import _norm
from fec.cleaning.work.status import is_real_employer


# Recovered companies go to previous_employer when still RETIRED.
def _settle_retired_employer(df: pd.DataFrame) -> int:
    required = {
        'entity_type', 'contributor_employer', 'contributor_occupation',
        'occupation_category',
    }
    if not required.issubset(df.columns):
        return 0

    employer = _norm(df['contributor_employer'])
    retired = (
        df['entity_type'].eq('INDIVIDUAL')
        & df['occupation_category'].eq('RETIRED')
        & _norm(df['contributor_occupation']).eq('RETIRED')
        & employer.ne('RETIRED')
    )
    count = int(retired.sum())
    if not count:
        return 0

    if 'previous_employer' not in df.columns:
        df['previous_employer'] = pd.NA
    previous_empty = _norm(df['previous_employer']).eq('')
    is_real_company = df['contributor_employer'].map(is_real_employer)
    copy = retired & previous_empty & is_real_company
    df.loc[copy, 'previous_employer'] = df.loc[copy, 'contributor_employer']

    df.loc[retired, 'contributor_employer'] = 'RETIRED'

    return count
