"""Read CSV values without losing names; replace completed output files."""

import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

# Keep NULL, NA and n/a as text: real names.
NA_VALUES = ["", "#N/A", "#NA", "N/A", "#N/A N/A", "NaN", "nan", "None"]


# Read strings to preserve leading zeros and avoid scientific notation.
ID_DTYPES = {
    "sub_id": "string",
    "transaction_id": "string",
    "committee_id": "string",
    "contributor_zip": "string",
    "employer_zip": "string",
}


def read_pipeline_csv(path: str | Path) -> pd.DataFrame:
    """Read a pipeline CSV, keeping ID leading zeros and surnames like NULL and NA."""
    return pd.read_csv(path, dtype=ID_DTYPES, low_memory=False, keep_default_na=False, na_values=NA_VALUES)


# Allow short Windows file locks to clear.
REPLACE_ATTEMPTS = 10

REPLACE_WAIT_SECONDS = 0.5


def replace_file(temp_path: str, path: str) -> None:
    """Replace the target after lock retries; on failure keep it."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt + 1 < REPLACE_ATTEMPTS:
                time.sleep(REPLACE_WAIT_SECONDS)
    with contextlib.suppress(OSError):
        os.remove(temp_path)
    raise PermissionError(
        f"{path} is open in another program (Excel, an editor, a viewer or a sync tool); "
        "close it and run the same command again. The file was left as it was."
    )


def write_json_atomic(path: str | Path, data: Any, **dump_options: Any) -> None:
    """Write JSON to a temporary file, then replace the target."""
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, **dump_options)
    replace_file(temp_path, path)


def write_csv_atomic(df: pd.DataFrame, path: str | Path, **to_csv_options: Any) -> None:
    """Write CSV to a temporary file, then replace the target."""
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    df.to_csv(temp_path, **to_csv_options)
    replace_file(temp_path, path)
