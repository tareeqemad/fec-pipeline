"""One run id from clean.py to the loader, so no stage mixes files of two runs.

clean.py starts a run and writes pipeline_run.json beside the cleaned CSV. Every
stage that writes a pipeline file records its SHA-256 under that run; a stage
that reads one first checks the file is exactly what this run last wrote. A
file restored from an older run, edited by hand, or left over from before the
last clean.py is refused.
"""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fec.io import write_json_atomic

MANIFEST_NAME = "pipeline_run.json"


class RunMismatch(RuntimeError):
    """A pipeline file is not the one the current run wrote."""


# the manifest path beside a pipeline file
def _manifest_path(path: Path) -> Path:
    return Path(path).parent / MANIFEST_NAME


# hex SHA-256 of a file, read in chunks
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# read the manifest, or raise with what to run
def _read(path: Path) -> dict:
    manifest = _manifest_path(path)
    if not manifest.exists():
        raise RunMismatch(f"no {MANIFEST_NAME} beside {Path(path).name}; run clean.py first")
    with open(manifest, encoding="utf-8") as handle:
        return json.load(handle)


# start a new run beside the cleaned CSV and return its id
def start_run(cleaned_csv: Path) -> str:
    now = datetime.now(timezone.utc)
    run_id = f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    write_json_atomic(_manifest_path(cleaned_csv), {
        "run_id": run_id, "started_at": now.isoformat(timespec="seconds"), "files": {},
    }, indent=2)
    return run_id


# record files a stage just wrote under the current run
def record(stage: str, *paths: Path) -> str:
    data = _read(paths[0])
    for path in paths:
        data["files"][Path(path).name] = {
            "stage": stage,
            "sha256": _sha256(path),
            "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    write_json_atomic(_manifest_path(paths[0]), data, indent=2)
    return data["run_id"]


# raise unless every file is exactly what the current run wrote
def check_same_run(*paths: Path) -> str:
    data = _read(paths[0])
    run_id = data["run_id"]
    for path in paths:
        name = Path(path).name
        entry = data["files"].get(name)
        if entry is None:
            raise RunMismatch(f"{name} was not written in run {run_id}; rerun the pipeline after clean.py")
        if not Path(path).exists() or _sha256(path) != entry["sha256"]:
            raise RunMismatch(
                f"{name} changed after {entry['stage']} wrote it in run {run_id}; "
                "rerun the pipeline from that stage"
            )
    return run_id
