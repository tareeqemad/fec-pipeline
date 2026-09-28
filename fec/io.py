"""CSV I/O helpers: "Null" is a real donor surname, so every pipeline read must use an na_values list that omits "NULL" (read_pipeline_csv centralises this)."""
import json
import os
import time
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


# a Windows reader (Excel, an editor, antivirus, OneDrive) can hold the target for a moment
REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SECONDS = 0.5


# move the finished temp file over the target, waiting out a short Windows lock
def replace_file(temp_path: str, path: str) -> None:
    """os.replace, retried while another program holds the target open.

    If the lock outlasts the retries the temp file is removed, the old file is
    left as it was, and the error names the file to close.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt + 1 < REPLACE_ATTEMPTS:
                time.sleep(REPLACE_WAIT_SECONDS)
    try:
        os.remove(temp_path)
    except OSError:
        pass
    raise PermissionError(
        f"{path} is open in another program (Excel, an editor, a viewer or a sync tool); "
        "close it and run the same command again. The file was left as it was."
    )


# write JSON via a temp file, never half-written
def write_json_atomic(path: str | Path, data, **dump_options) -> None:
    """Write data as JSON to path via path + '.tmp' and an atomic rename."""
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, **dump_options)
    replace_file(temp_path, path)


# write a DataFrame as CSV through a temp file and an atomic rename
def write_csv_atomic(df, path: str | Path, **to_csv_options) -> None:
    """A reader never sees half a file: a failed write leaves the old one."""
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    df.to_csv(temp_path, **to_csv_options)
    replace_file(temp_path, path)
