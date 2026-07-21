"""
manual_overrides.py — hand-curated per-row employer corrections.

Some employer fixes cannot be expressed as a global synonym because the
correct value depends on the specific donor, not just the employer string.

Example: a row with employer "JEWISH FEDERATION" must resolve to the
*local* federation based on the donor's city — "JEWISH FEDERATION OF
GREATER HOUSTON" for a Houston donor, "JEWISH FEDERATION OF OCEAN COUNTY"
for an Ocean NJ donor. A global EMPLOYER_SYNONYMS entry can't do that
(it would map every "JEWISH FEDERATION" to one place).

These corrections live in data/manual_employer_overrides.csv, keyed by
`sub_id` (FEC's stable unique transaction id). clean.py applies them as
the LAST step so they survive every re-clean — no per-row data is lost
when the pipeline regenerates contributions_cleaned.csv from scratch.

`contributor_occupation` is optional and used for the swap case: a filer who
put the job title in the employer field and the COMPANY in the occupation field
("CHAIRMAN" / "KIMCO"). The generic swap-back can't fire because its company
detector doesn't recognise a bare brand name, so the pair is corrected by hand.
Either column may be given on its own.

CSV format:
    sub_id,contributor_employer,contributor_occupation,note
    4032520241885655422,JEWISH FEDERATION OF GREATER HOUSTON,,"..."
"""
from __future__ import annotations

import csv

from fec.env import PROJECT_ROOT
from fec.log import get_logger

logger = get_logger(__name__)

OVERRIDES_CSV = PROJECT_ROOT / "data" / "manual_employer_overrides.csv"


def apply_manual_employer_overrides(df) -> int:
    """Apply per-row employer overrides from manual_employer_overrides.csv.

    Matches on `sub_id`. Returns the number of rows changed.
    Safe no-op if the file is missing or empty.
    """
    if not OVERRIDES_CSV.exists():
        return 0

    overrides: dict[str, dict[str, str]] = {}
    with OVERRIDES_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sid = (row.get("sub_id") or "").strip()
            fields = {
                col: (row.get(col) or "").strip()
                for col in ("contributor_employer", "contributor_occupation")
                if (row.get(col) or "").strip()
            }
            if sid and fields:
                overrides[sid] = fields

    if not overrides:
        return 0

    if "sub_id" not in df.columns:
        logger.warning("  ⚠ manual_overrides: no sub_id column — skipping")
        return 0

    sub_id_str = df["sub_id"].astype(str).str.strip()
    n_changed = 0
    n_missing = 0
    for sid, fields in overrides.items():
        mask = sub_id_str == sid
        if mask.any():
            for col, val in fields.items():
                if col in df.columns:
                    df.loc[mask, col] = val
            n_changed += int(mask.sum())
        else:
            n_missing += 1

    if n_missing:
        logger.info(
            f"  manual_overrides: {n_missing} sub_id(s) in the override file "
            f"not found in this dataset (may be from a different committee/period)"
        )
    return n_changed
