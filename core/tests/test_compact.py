"""The index gives free space back (PLAN3 P0-8)."""

import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")

from raven_core import db, maintenance  # noqa: E402

db.init()


def _fill_and_delete(n: int = 4000) -> None:
    with db.tx() as c:
        c.executemany("INSERT INTO files(path, name, parent) VALUES (?, ?, ?)",
                      [(f"/compact-test/{i:06d}-{'x' * 200}", f"f{i}", "/compact-test") for i in range(n)])
    with db.tx() as c:
        c.execute("DELETE FROM files WHERE parent='/compact-test'")


def _run(code: str, data: str) -> subprocess.CompletedProcess:
    e = {**os.environ, "RAVEN_DATA_DIR": data, "XDG_DATA_HOME": os.path.join(data, "xdg")}
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, timeout=60)


def test_new_index_uses_incremental_auto_vacuum():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    r = _run("from raven_core import db; db.init(); print(db.size_info()['auto_vacuum'])", data)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "incremental"


def test_old_index_switches_mode_at_its_first_compact():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    os.makedirs(data)
    sqlite3.connect(os.path.join(data, "raven.db")).execute("CREATE TABLE old(x)").connection.close()
    code = ("from raven_core import db; db.init(); a = db.size_info()['auto_vacuum']; db.compact(); "
            "print(a, db.size_info()['auto_vacuum'])")
    r = _run(code, data)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["none", "incremental"]


def test_compact_frees_the_deleted_rows():
    _fill_and_delete()
    before = db.size_info()
    assert before["free_bytes"] > 0
    out = db.compact()
    assert out["after"]["free_bytes"] == 0
    assert out["after"]["bytes"] < before["bytes"]
    assert out["after"]["wal_bytes"] == 0


def test_trim_gives_pages_back_on_an_incremental_index():
    db.compact()  # incremental mode from here on
    _fill_and_delete()
    assert db.size_info()["free_bytes"] > 0
    assert db.trim() > 0
    assert db.size_info()["free_bytes"] == 0


def test_idle_run_skips_a_small_free_share(monkeypatch):
    monkeypatch.setattr(db, "size_info", lambda: {"bytes": 10, "free_bytes": 0, "free_share": 0.01, "wal_bytes": 0,
                                                  "auto_vacuum": "none"})
    assert maintenance.run_once()["action"] == "none"


@pytest.mark.parametrize("route", ["/index/size"])
def test_size_route(route):
    from fastapi.testclient import TestClient

    from raven_core import app as app_mod

    r = TestClient(app_mod.app).get(route, headers={"Authorization": f"Bearer {app_mod.TOKEN}"})
    assert r.status_code == 200
    assert {"bytes", "free_bytes", "auto_vacuum"} <= set(r.json())
