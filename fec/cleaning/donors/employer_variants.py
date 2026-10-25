"""Apply documented company aliases after donor identity is established."""
import pandas as pd

from fec.cleaning.work.employer_aliases import reviewed_employer_name


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """An organization's display name needs the same explicit proof."""
    mask = df["entity_type"].eq("ORGANIZATION") & df["contributor_name"].notna()
    names = df.loc[mask, "contributor_name"]
    reviewed = names.map(reviewed_employer_name)
    changed = reviewed.ne(names)
    df.loc[names.index[changed], "contributor_name"] = reviewed[changed]
    return int(changed.sum())
