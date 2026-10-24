"""Apply documented company aliases after donor identity is established."""
import pandas as pd

from fec.cleaning.work.employer_aliases import (
    apply_reviewed_employer_names,
    reviewed_employer_name,
)


def _employer_variant_map(names: list[str]) -> dict[str, str]:
    """Similar words alone do not establish company identity."""
    return {name: target for name in names
            if (target := reviewed_employer_name(name)) != name}


def canonicalize_donor_employers(df: pd.DataFrame) -> int:
    return apply_reviewed_employer_names(df, ("contributor_employer",))


def _employer_typos(df: pd.DataFrame) -> int:
    return apply_reviewed_employer_names(df)


def _employer_substring_variants(df: pd.DataFrame) -> int:
    return apply_reviewed_employer_names(df)


def _employer_acronym_variants(df: pd.DataFrame) -> int:
    return apply_reviewed_employer_names(df)


def align_org_donor_company_names(df: pd.DataFrame) -> int:
    """An organization's display name needs the same explicit proof."""
    mask = df["entity_type"].eq("ORGANIZATION") & df["contributor_name"].notna()
    names = df.loc[mask, "contributor_name"]
    reviewed = names.map(reviewed_employer_name)
    changed = reviewed.ne(names)
    df.loc[names.index[changed], "contributor_name"] = reviewed[changed]
    return int(changed.sum())


def unify_org_donor_suffix_variants(df: pd.DataFrame) -> int:
    return align_org_donor_company_names(df)
