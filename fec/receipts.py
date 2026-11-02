"""Read the newest complete receipt snapshot for each refreshed committee and cycle."""
import re
from pathlib import Path

import pandas as pd

from fec.cleaning.entities import attach_source_fields
from fec.env import FEC_SOURCE_CSV
from fec.io import read_pipeline_csv
from fec.pull_rows import COLUMNS, ENTITY_FIELDS, LINE_FIELDS, SNAPSHOT_COLUMNS, SOURCE_FIELDS
from fec.receipt_types import classify_receipts


def read_current_receipts(raw_path: str | Path) -> pd.DataFrame:
    """Use the original raw file only for scopes without a published snapshot."""
    raw_path = Path(raw_path)
    snapshots = {}
    for path in sorted((raw_path.parent / "snapshots").glob("*/*.csv")):
        if path.name.startswith(".") or not path.is_file():
            continue
        scope = path.parent.name
        if not re.fullmatch(r"C\d{8}-\d{4}", scope):
            raise ValueError(f"Invalid receipt snapshot scope: {scope}")
        snapshots[scope] = path  # filenames sort by scan start time

    frames = []
    if raw_path.exists():
        legacy = read_pipeline_csv(raw_path)
        scopes = legacy["committee_id"].astype("string") + "-" + legacy["two_year_transaction_period"].astype("string")
        legacy = legacy.loc[~scopes.isin(snapshots)].copy()
        attach_source_fields(legacy, raw_path.with_name(FEC_SOURCE_CSV.name))
        frames.append(legacy)
    elif not snapshots:
        raise FileNotFoundError(f"No receipts found at {raw_path} or in its snapshots directory")

    for scope, path in snapshots.items():
        frame = read_pipeline_csv(path)
        if list(frame.columns) not in (SNAPSHOT_COLUMNS, [*COLUMNS, *SOURCE_FIELDS], [*COLUMNS, *ENTITY_FIELDS]):
            raise ValueError(f"Unexpected receipt snapshot columns in {path}")
        committee, cycle = scope.split("-")
        in_scope = frame["committee_id"].eq(committee) & frame["two_year_transaction_period"].astype("string").eq(cycle)
        if not in_scope.fillna(False).all():
            raise ValueError(f"Receipt outside {scope} in {path}")
        for field in SOURCE_FIELDS.keys() & set(frame.columns):
            frame[field] = frame[field].fillna("")
        frames.append(frame)

    older = [i for i, frame in enumerate(frames) if not frame.empty and
             "receipt_kind" not in frame and not set(LINE_FIELDS).issubset(frame)]
    lines_path = raw_path.with_name("fec_receipt_lines.csv")
    if older and lines_path.exists():
        lines = read_pipeline_csv(lines_path)
        keys = ["sub_id", "fec_image_number"]
        if (list(lines) != [*keys, *LINE_FIELDS, "source"]
                or not all(lines[k].str.fullmatch(r"[0-9]+", na=False).all() for k in keys)
                or lines["sub_id"].duplicated().any() or lines["source"].isna().any()):
            raise ValueError(f"Invalid receipt line evidence in {lines_path}")
        evidence = lines.drop(columns="source")
        for i in older:
            frames[i] = frames[i].merge(evidence, on=keys, how="left", validate="many_to_one")

    for frame in frames:
        frame["receipt_kind"] = classify_receipts(frame)
    current = pd.concat(frames, ignore_index=True)
    ids = current["sub_id"].astype("string")
    if not ids.str.fullmatch(r"[0-9]+", na=False).all() or ids.duplicated().any():
        raise ValueError("Current receipts contain a missing, invalid or duplicate sub_id")
    return current
