"""Apply each filing's employer status and previous employer."""

import pandas as pd
from fec.cleaning.donors.retired import dated_previous_employers
from fec.cleaning.work.employer_canonical import canonical_key
from fec.cleaning.work.employer_aliases import apply_reviewed_employer_names, scoped_employer_rules
from fec.cleaning.work.employer_names import expand_abbreviation_text
from fec.resolve.manual import previous_employer_display_names
from fec.resolve.rows import ResolveContext, _first_filed_employers, _resolve_row
from fec.cleaning.work.previous import normalize_previous_employer_column


def apply_results(df: pd.DataFrame, prev_cache, filed_employers: dict | None = None) -> pd.DataFrame:
    """filed_employers preserves each filing's own employer."""
    prior_previous = df["previous_employer"].copy()

    columns = ("employer_status", "previous_employer")
    cols = {column: [] for column in columns}

    retired_rows = df["entity_type"].eq("INDIVIDUAL") & df["contributor_employer"].eq("RETIRED")
    dated = dated_previous_employers(df, retired_rows)
    first_filed = _first_filed_employers(df)
    context = ResolveContext(
        previous_cache=prev_cache,
        dated_previous=dated,
        first_filed=first_filed,
        filed_employers=filed_employers or {},
        previous_display_names=previous_employer_display_names(),
        scoped_employer_rules=scoped_employer_rules(df),
    )
    for index, row in df.iterrows():
        result = _resolve_row(row, context, index)
        for column in cols:
            cols[column].append(result.get(column, ""))

    for column, values in cols.items():
        df[column] = values

    normalize_previous_employer_column(df)
    _preserve_previous_employer_display(df, prior_previous)
    _expand_previous_employer_abbreviations(df)
    apply_reviewed_employer_names(df)
    return df


def _expand_previous_employer_abbreviations(df: pd.DataFrame) -> None:
    """Apply reviewed names to previous employers."""
    previous = df["previous_employer"].fillna("").astype(str)
    named = previous.str.strip().ne("")
    if named.any():
        df.loc[named, "previous_employer"] = expand_abbreviation_text(previous[named])


def _preserve_previous_employer_display(
    df: pd.DataFrame,
    prior_previous: pd.Series,
) -> None:
    """Keep the display spelling when resolve found the same company."""
    # Keep the existing uppercase spelling.
    prior = prior_previous.fillna("").astype(str).str.strip().str.upper()
    current = df["previous_employer"].fillna("").astype(str).str.strip()
    prior_key = prior.map(canonical_key)
    same_company = prior_key.ne("") & current.map(canonical_key).eq(prior_key)
    if same_company.any():
        df.loc[same_company, "previous_employer"] = prior[same_company]
