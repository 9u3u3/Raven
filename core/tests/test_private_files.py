"""The data folder is private, and the token stays out of argv and child processes (PLAN3 P0-1, P0-2)."""

import os
import stat
import subprocess
import sys
import tempfile

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX modes")


def _run(code: str, data: str, **env) -> subprocess.CompletedProcess:
    e = {**os.environ, "RAVEN_DATA_DIR": data, "XDG_DATA_HOME": os.path.join(data, "xdg"), **env}
    return subprocess.run([sys.executable, "-c", code], env=e, capture_output=True, text=True, timeout=60)


def _mode(p: str) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_new_data_folder_is_private():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    r = _run("from raven_core import config", data)
    assert r.returncode == 0, r.stderr
    assert _mode(data) == 0o700
    assert _mode(os.path.join(data, "thumbs")) == 0o700


def test_old_data_folder_is_made_private():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    os.makedirs(data, mode=0o755)
    os.chmod(data, 0o755)
    for name in ("raven.db", "raven.db-wal"):
        with open(os.path.join(data, name), "w"):
            pass
        os.chmod(os.path.join(data, name), 0o644)
    r = _run("from raven_core import config; config.protect_data_files()", data)
    assert r.returncode == 0, r.stderr
    assert _mode(data) == 0o700
    assert _mode(os.path.join(data, "raven.db")) == 0o600
    assert _mode(os.path.join(data, "raven.db-wal")) == 0o600


def test_child_processes_do_not_get_the_token():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    code = "import os; from raven_core import app; print(bool(app.TOKEN), 'RAVEN_TOKEN' in os.environ)"
    r = _run(code, data, RAVEN_TOKEN="secret-token")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["True", "False"]


def test_second_sidecar_on_one_data_folder_exits():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    hold = ("import sys, time; from raven_core import __main__ as m; m._lock_data_dir(); print('locked', flush=True); "
            "time.sleep(30)")
    e = {**os.environ, "RAVEN_DATA_DIR": data, "XDG_DATA_HOME": os.path.join(data, "xdg")}
    first = subprocess.Popen([sys.executable, "-c", hold], env=e, stdout=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "locked"
        r = _run("from raven_core import __main__ as m; m._lock_data_dir()", data)
        assert r.returncode != 0
        assert "another sidecar uses" in r.stderr
    finally:
        first.kill()
        first.wait()
    # The lock is free again after the first process ends.
    r = _run("from raven_core import __main__ as m; m._lock_data_dir()", data)
    assert r.returncode == 0, r.stderr


def test_database_files_are_private():
    data = os.path.join(tempfile.mkdtemp(prefix="raven-test-"), "data")
    r = _run("from raven_core import db; db.init(); db.set_setting('x', 1)", data)
    assert r.returncode == 0, r.stderr
    for name in os.listdir(data):
        if name.startswith("raven.db"):
            assert _mode(os.path.join(data, name)) == 0o600, name
