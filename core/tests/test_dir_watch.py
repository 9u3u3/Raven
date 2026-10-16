"""The open-folder watcher skips noise and limits events (PLAN3 P0-11)."""

import os
import tempfile
import time

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from watchfiles import Change  # noqa: E402

from raven_core import app as app_mod  # noqa: E402
from raven_core import db  # noqa: E402

db.init()


def test_filter_skips_hidden_and_temporary_files():
    f = app_mod._dir_watch_filter
    assert f(Change.added, "/x/report.pdf")
    for name in (".bash_history", ".bash_history-01234.tmp", "video.mp4.part", "a.crdownload", ".#notes.md",
                 "~$Book1.xlsx", "notes.md~", "x.swp"):
        assert not f(Change.modified, f"/x/{name}"), name
    db.set_setting("show_hidden", True)
    try:
        assert f(Change.added, "/x/.env")
        assert not f(Change.added, "/x/.bash_history-1.tmp")
    finally:
        db.set_setting("show_hidden", False)


def test_new_names_send_events_and_modified_files_are_limited(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(app_mod.events, "publish", lambda kind, **kw: sent.append(kind))
    monkeypatch.setattr(app_mod, "DIR_MODIFIED_EVERY", 60.0)
    (tmp_path / "busy.log").write_text("0")
    w = app_mod.DirWatch()
    w.set(str(tmp_path))
    try:
        time.sleep(0.5)
        (tmp_path / "new.txt").write_text("x")  # a new name: one event
        time.sleep(2.0)
        assert sent.count("dir_changed") == 1
        for i in range(6):  # a file that keeps changing: no event within DIR_MODIFIED_EVERY
            (tmp_path / "busy.log").write_text(str(i))
            time.sleep(0.3)
        time.sleep(1.5)
        assert sent.count("dir_changed") == 1
        (tmp_path / ".hidden").write_text("x")  # hidden: no event
        time.sleep(1.5)
        assert sent.count("dir_changed") == 1
    finally:
        w.stop()
