"""CSV I/O helpers: "Null" is a real donor surname, so every pipeline read must use an na_values list that omits "NULL" (read_pipeline_csv centralises this)."""
from pathlib import Path

import pandas as pd


# pandas' default missing-value list minus 'NULL', 'NA', 'null', 'n/a'
# (real surnames like "Null" and "Na"); junk placeholders are still recognised
NA_VALUES = ['', '#N/A', '#NA', 'N/A', '#N/A N/A', 'NaN', 'nan', 'None']


# columns read as strings to preserve leading zeros and avoid scientific notation
ID_DTYPES = {
    'sub_id': 'string',
    'transaction_id': 'string',
    'committee_id': 'string',
    'contributor_zip': 'string',
    'employer_zip': 'string',
}


def read_pipeline_csv(
    path: str | Path,
    extra_dtypes: dict | None = None,
    all_string: bool = False,
    **kwargs,
) -> pd.DataFrame:
    """Read a pipeline CSV with NULL-surname-safe defaults; dtype/keep_default_na/na_values are reserved, extra kwargs go to pd.read_csv."""
    for reserved in ('keep_default_na', 'na_values'):
        if reserved in kwargs:
            raise TypeError(
                f"read_pipeline_csv: {reserved!r} is managed internally; "
                "pass additional na values via a custom read_csv instead."
            )

    # peek header to scope dtypes to present columns only
    peek = pd.read_csv(path, nrows=0)
    present = set(peek.columns)

    if all_string:
        dtypes: dict = {c: str for c in present}
    else:
        dtypes = {k: v for k, v in ID_DTYPES.items() if k in present}

    if extra_dtypes:
        dtypes.update({k: v for k, v in extra_dtypes.items() if k in present})

    return pd.read_csv(
        path,
        dtype=dtypes if dtypes else None,
        low_memory=False,
        keep_default_na=False,
        na_values=NA_VALUES,
        **kwargs,
    )
