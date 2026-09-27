"""tools/move_data_layout.py moves old flat data/ files and never overwrites a new one."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _tool(data, monkeypatch):
    spec = importlib.util.spec_from_file_location("move_data_layout", REPO / "tools" / "move_data_layout.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA", data)
    return module


def test_old_files_move_and_a_taken_place_is_left(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "database").mkdir(parents=True)
    (data / "_review" / "sub").mkdir(parents=True)
    (data / "audit_changes.csv").write_text("old audit", encoding="utf-8")
    (data / "database" / "leaders.csv").write_text("roster", encoding="utf-8")
    (data / "_review" / "sub" / "flags.csv").write_text("flags", encoding="utf-8")
    (data / "geocode_cache.json").write_text("local cache", encoding="utf-8")
    (data / "cache").mkdir()
    (data / "cache" / "geocode_cache.json").write_text("pulled cache", encoding="utf-8")

    code = _tool(data, monkeypatch).main([])

    assert code == 1
    assert (data / "reports" / "audit_changes.csv").read_text(encoding="utf-8") == "old audit"
    assert (data / "rules" / "leaders.csv").read_text(encoding="utf-8") == "roster"
    assert (data / "reports" / "review" / "sub" / "flags.csv").exists()
    assert not (data / "database").exists() and not (data / "_review").exists()
    assert (data / "cache" / "geocode_cache.json").read_text(encoding="utf-8") == "pulled cache"
    assert (data / "geocode_cache.json").read_text(encoding="utf-8") == "local cache"


def test_dry_run_moves_nothing(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "contributions.csv").write_text("raw", encoding="utf-8")

    assert _tool(data, monkeypatch).main(["--dry-run"]) == 0
    assert (data / "contributions.csv").exists() and not (data / "raw").exists()
