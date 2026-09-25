"""CSV I/O helpers: "Null" is a real donor surname, so every pipeline read must use an na_values list that omits "NULL" (read_pipeline_csv centralises this)."""
import json
import os
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


# read a pipeline CSV with NULL-surname-safe defaults
def read_pipeline_csv(path: str | Path) -> pd.DataFrame:
    """Read a pipeline CSV with NULL-surname-safe defaults."""
    # peek header to scope dtypes to present columns only
    peek = pd.read_csv(path, nrows=0)
    present = set(peek.columns)
    dtypes = {k: v for k, v in ID_DTYPES.items() if k in present}

    return pd.read_csv(
        path,
        dtype=dtypes if dtypes else None,
        low_memory=False,
        keep_default_na=False,
        na_values=NA_VALUES,
    )


# write JSON via a temp file, never half-written
def write_json_atomic(path: str | Path, data, **dump_options) -> None:
    """Write data as JSON to path via path + '.tmp' and an atomic rename."""
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, **dump_options)
    os.replace(temp_path, path)
