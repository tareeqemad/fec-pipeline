"""A target another program holds open (Windows) is retried, then left as it was."""
import pandas as pd
import pytest

from fec import io


def test_a_short_lock_is_waited_out(tmp_path, monkeypatch):
    path = tmp_path / "out.csv"
    real_replace, calls = io.os.replace, []

    def locked_twice(src, dst):
        calls.append(dst)
        if len(calls) < 3:
            raise PermissionError("WinError 5")
        real_replace(src, dst)

    monkeypatch.setattr(io.os, "replace", locked_twice)
    monkeypatch.setattr(io, "REPLACE_WAIT_SECONDS", 0)
    io.write_csv_atomic(pd.DataFrame({"a": [1]}), path, index=False)
    assert len(calls) == 3 and path.read_text() == "a\n1\n"


def test_a_lasting_lock_keeps_the_old_file_and_names_it(tmp_path, monkeypatch):
    path = tmp_path / "out.csv"
    path.write_text("old\n")

    def locked(src, dst):
        raise PermissionError("WinError 5")

    monkeypatch.setattr(io.os, "replace", locked)
    monkeypatch.setattr(io, "REPLACE_WAIT_SECONDS", 0)
    with pytest.raises(PermissionError, match="out.csv is open in another program"):
        io.write_csv_atomic(pd.DataFrame({"a": [1]}), path, index=False)
    assert path.read_text() == "old\n"
    assert not (tmp_path / "out.csv.tmp").exists()
