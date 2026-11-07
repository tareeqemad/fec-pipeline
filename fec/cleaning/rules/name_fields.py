"""Safety nets for names filed in the wrong work field."""
from __future__ import annotations

import pandas as pd

from fec.cleaning.rules.own_name import _had_legal_suffix, _is_own_name
from fec.config.constants import SKIP_EMPLOYERS


# employer equal to donor's own name becomes self-employed
def _fix_own_name_as_employer(df: pd.DataFrame) -> int:
    """Employer is the donor's full name: SELF-EMPLOYED."""
    is_indiv = df['entity_type'] == 'INDIVIDUAL'
    emp = df['contributor_employer'].fillna('')
    mask = is_indiv & emp.ne('') & ~emp.isin(SKIP_EMPLOYERS)
    if not mask.any():
        return 0

    hits = []
    for idx in df.index[mask]:
        if _is_own_name(df, idx, emp.at[idx]) and not _had_legal_suffix(df, idx):
            hits.append(idx)

    if hits:
        df.loc[hits, 'contributor_employer'] = 'SELF-EMPLOYED'
    return len(hits)
