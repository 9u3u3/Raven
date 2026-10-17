"""The watcher: one watch per indexed folder, no watch for skipped or unreadable folders."""

import os
import tempfile
import threading
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core.index import indexer as ix  # noqa: E402


def test_watch_dirs_skip_dependencies_and_limit(tmp_path):
    for d in ("a/x", "a/node_modules/pkg/lib", "b", ".cache/big"):
        (tmp_path / d).mkdir(parents=True)
    dirs, full = ix.watch_dirs([str(tmp_path)], 100)
    assert set(dirs) == {str(tmp_path), str(tmp_path / "a"), str(tmp_path / "a/x"), str(tmp_path / "b")}
    assert not full
    dirs, full = ix.watch_dirs([str(tmp_path)], 2)
    assert full and dirs[0] == str(tmp_path) and len(dirs) == 2


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="root can read every folder, and Windows has no chmod 0")
def test_watcher_survives_an_unreadable_folder(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    locked = tmp_path / "lost+found"
    locked.mkdir()
    locked.chmod(0)
    seen: list[str] = []
    got = threading.Event()

    def update(paths):
        seen.extend(paths)
        got.set()

    monkeypatch.setattr(ix, "roots", lambda: [str(tmp_path)])
    idx = ix.Indexer.__new__(ix.Indexer)
    idx._watch_stop = threading.Event()
    idx.stop = threading.Event()
    idx.update_paths = update
    try:
        idx._start_watcher()
        time.sleep(1.5)
        (tmp_path / "docs" / "new.txt").write_text("hello")
        (tmp_path / "top.txt").write_text("hello")
        assert got.wait(10)
        time.sleep(1)
        assert str(tmp_path / "docs" / "new.txt") in seen
        assert str(tmp_path / "top.txt") in seen
    finally:
        idx._watch_stop.set()
        locked.chmod(0o700)


def test_new_folder_gets_a_watch(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(ix, "roots", lambda: [str(tmp_path)])
    idx = ix.Indexer.__new__(ix.Indexer)
    idx._watch_stop = threading.Event()
    idx.stop = threading.Event()
    idx.update_paths = seen.extend
    monkeypatch.setattr(idx, "_rewatch_later", lambda: idx._start_watcher(), raising=False)
    try:
        idx._start_watcher()
        time.sleep(1.5)
        (tmp_path / "new").mkdir()
        time.sleep(2.5)
        (tmp_path / "new" / "inside.txt").write_text("x")
        end = time.time() + 10
        while str(tmp_path / "new" / "inside.txt") not in seen and time.time() < end:
            time.sleep(0.2)
        assert str(tmp_path / "new" / "inside.txt") in seen
    finally:
        idx._watch_stop.set()
