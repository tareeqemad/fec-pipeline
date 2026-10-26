"""Read CSV values without losing names; replace completed output files."""
import json
import os
import time
from pathlib import Path

import pandas as pd

# Keep NULL, NA, null and n/a as text: they can be real names.
NA_VALUES = ['', '#N/A', '#NA', 'N/A', '#N/A N/A', 'NaN', 'nan', 'None']


# Read strings to preserve leading zeros and avoid scientific notation.
ID_DTYPES = {
    'sub_id': 'string',
    'transaction_id': 'string',
    'committee_id': 'string',
    'contributor_zip': 'string',
    'employer_zip': 'string',
}


# Keep ID leading zeros and real surnames such as NULL and NA.
def read_pipeline_csv(path: str | Path) -> pd.DataFrame:
    # Apply dtypes only to columns present in the header.
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


# Allow short Windows file locks to clear.
REPLACE_ATTEMPTS = 10
REPLACE_WAIT_SECONDS = 0.5


# Replace the target after short lock retries; keep the old file on failure.
def replace_file(temp_path: str, path: str) -> None:
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


# Write JSON to a temporary file, then replace the target.
def write_json_atomic(path: str | Path, data, **dump_options) -> None:
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, **dump_options)
    replace_file(temp_path, path)


# Write CSV to a temporary file, then replace the target.
def write_csv_atomic(df, path: str | Path, **to_csv_options) -> None:
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    df.to_csv(temp_path, **to_csv_options)
    replace_file(temp_path, path)
