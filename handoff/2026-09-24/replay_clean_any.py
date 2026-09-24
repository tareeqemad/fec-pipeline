"""Run the full clean stage of ANY checkout into a scratch directory (never touches that checkout's data/ outputs).

Usage: python replay_clean_any.py <out_dir> <repo_dir>
Writes <out_dir>/contributions_cleaned.csv, audit_changes.csv, audit_summary.json and the review files.
The FEC address-recovery cache is copied from <repo_dir>/data first; no .env is loaded, so the run makes no
network calls and two replays of the same data are comparable byte for byte.
"""
import os
import shutil
import sys

out_dir = os.path.abspath(sys.argv[1])
REPO = os.path.abspath(sys.argv[2])
if os.path.commonpath([out_dir, os.path.join(REPO, "data")]) == os.path.join(REPO, "data"):
    sys.exit("refusing to write inside data/")
sys.path.insert(0, REPO)
os.chdir(REPO)
os.makedirs(out_dir, exist_ok=True)
cache = os.path.join(REPO, "data", "fec_address_cache.json")
if os.path.exists(cache) and not os.path.exists(os.path.join(out_dir, "fec_address_cache.json")):
    shutil.copy(cache, out_dir)
os.environ.pop("FEC_API_KEY", None)

import fec  # noqa: E402
assert os.path.abspath(fec.__file__).startswith(REPO), fec.__file__

from fec.env import RAW_CSV  # noqa: E402
from fec.io import read_pipeline_csv  # noqa: E402
from fec.log import setup_logging  # noqa: E402
from fec.cleaning.audit import write_audit  # noqa: E402
from fec.cleaning.cli import _drop_internal_cols, _ensure_zip_format  # noqa: E402
from fec.cleaning.pipeline import clean_pipeline  # noqa: E402

setup_logging()
df = read_pipeline_csv(str(RAW_CSV))
original_rows = df[["sub_id"]].copy()
original_rows["row_index"] = original_rows.index.astype(int)
cleaned, missing, trail = clean_pipeline(df, out_dir=out_dir)
output = _drop_internal_cols(cleaned).copy()
_ensure_zip_format(output)
output.to_csv(os.path.join(out_dir, "contributions_cleaned.csv"), index=False)
write_audit(cleaned, original_rows, out_dir, trail)
print("replay written to", out_dir)
