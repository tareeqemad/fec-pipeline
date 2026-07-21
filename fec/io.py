"""CSV I/O helpers for the pipeline.

The FEC dataset contains real donors whose surname is literally "Null"
(e.g. "NULL, JAMES" — 4 records as of 2026-04). Pandas' default
`read_csv` treats the string `"NULL"` as a missing-value sentinel and
silently converts it to NaN, which then writes out as empty — erasing
the surname of real people. Every read in the pipeline must opt into
an explicit `na_values` list that omits "NULL".

`read_pipeline_csv()` centralises that config so no individual script
can forget it. It also applies the standard string dtypes for ID / ZIP
columns (leading-zero preservation).
"""
from pathlib import Path
from typing import Optional, Union

import pandas as pd


# Values pandas should treat as missing.  The canonical pandas default
# list is ['', '#N/A', '#N/A N/A', '#NA', '-1.#IND', '-1.#QNAN', '-NaN',
# '-nan', '1.#IND', '1.#QNAN', '<NA>', 'N/A', 'NA', 'NULL', 'NaN',
# 'None', 'n/a', 'nan', 'null'] — we drop 'NULL', 'NA', 'null', 'n/a'
# because surnames like "Null" and "Na" are real names; we still
# recognise the junk placeholders 'N/A' / 'NaN' / 'None' etc.
NA_VALUES = ['', '#N/A', '#NA', 'N/A', '#N/A N/A', 'NaN', 'nan', 'None']


# Columns that must be read as strings (not coerced to int/float), to
# preserve leading zeros and avoid scientific-notation corruption.
ID_DTYPES = {
    'sub_id': 'string',
    'transaction_id': 'string',
    'committee_id': 'string',
    'contributor_zip': 'string',
    'employer_zip': 'string',   # leading-zero ZIPs (08816) corrupt to float without this
}


def read_pipeline_csv(
    path: Union[str, Path],
    extra_dtypes: Optional[dict] = None,
    all_string: bool = False,
    **kwargs,
) -> pd.DataFrame:
    """Read a pipeline CSV with NULL-surname-safe defaults.

    Args:
        path: CSV file path.
        extra_dtypes: Additional column dtypes to apply (merged with
            the default ID_DTYPES).
        all_string: If True, read every column as `str`. ID_DTYPES is
            still applied (string dtype is compatible).
        **kwargs: Forwarded to `pd.read_csv` (e.g. `usecols`, `nrows`).
            `dtype`, `keep_default_na`, and `na_values` are reserved.

    Returns:
        DataFrame. Empty cells → NaN; literal "NULL" stays as "NULL".
    """
    for reserved in ('keep_default_na', 'na_values'):
        if reserved in kwargs:
            raise TypeError(
                f"read_pipeline_csv: {reserved!r} is managed internally; "
                "pass additional na values via a custom read_csv instead."
            )

    # Peek header to scope dtypes to present columns only
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
