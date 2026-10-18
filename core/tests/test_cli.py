"""The terminal search (cli.py): result order, the target folder, the shell setup and the sidecar discovery."""

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import pytest  # noqa: E402

from raven_core import cli  # noqa: E402


def _folder(path, **ranks):
    return {"path": path, "name": os.path.basename(path), "parent": os.path.dirname(path), "why": "name",
            "ranks": ranks}


def _file(path, is_dir=False):
    return {"path": path, "name": os.path.basename(path), "parent": os.path.dirname(path), "is_dir": is_dir,
            "why": "keyword"}


def test_files_first_unless_a_folder_name_ranks_first():
    files = [_file("/h/a/notes.md"), _file("/h/b/plan.md")]
    out = cli.merge("notes", [_folder("/h/a", keyword=1)], files)
    assert [o["path"] for o in out] == ["/h/a/notes.md", "/h/b/plan.md", "/h/a"]
    out = cli.merge("notes", [_folder("/h/notes", name=1)], files)
    assert out[0]["path"] == "/h/notes" and out[0]["is_dir"]
    out = cli.merge("~/Doc", [_folder("/h/Documents")], files)
    assert out[0]["path"] == "/h/Documents"
    out = cli.merge("thesis folder", [_folder("/h/thesis")], files)
    assert out[0]["path"] == "/h/thesis"


def test_merge_drops_a_path_twice():
    out = cli.merge("x", [_folder("/h/x", name=1)], [_file("/h/x", is_dir=True), _file("/h/x/y.txt")])
    assert [o["path"] for o in out] == ["/h/x", "/h/x/y.txt"]


def test_target_dir_is_the_folder_or_the_parent():
    assert cli.target_dir(_file("/h/a/notes.md")) == "/h/a"
    assert cli.target_dir(_file("/h/a", is_dir=True)) == "/h/a"


def test_fit_keeps_the_end_of_a_path():
    assert cli.fit("/home/me/Documents/report.pdf", 12, left=True) == "…/report.pdf"
    assert cli.fit("abcdef", 4) == "abc…"
    assert cli.fit("abc", 4) == "abc"
    assert cli.width("日本") == 4


@pytest.mark.parametrize("shell,key", [("bash", r"\C-k"), ("zsh", "^K"), ("fish", r"\ck")])
def test_init_defines_rv_and_binds_ctrl_k(shell, key):
    s = cli.init_script(shell)
    assert "rv" in s and "search" in s and "cd -- " in s
    assert key in s
    assert key not in cli.init_script(shell, key=False)


def test_init_refuses_an_unknown_shell():
    with pytest.raises(SystemExit):
        cli.init_script("tcsh")


class _Fake(BaseHTTPRequestHandler):
    locked = False

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer tok":
            self.send_response(401)
            self.end_headers()
            return
        body = json.dumps({"ok": True, "locked": _Fake.locked}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def test_connect_uses_the_running_sidecar(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        os.makedirs(cli.DATA_DIR, exist_ok=True)
        with open(os.path.join(cli.DATA_DIR, "sidecar.json"), "w", encoding="utf-8") as f:
            json.dump({"port": srv.server_address[1], "token": "tok", "pid": 1}, f)
        monkeypatch.setattr(cli, "_start_sidecar", lambda status: pytest.fail("must not start a sidecar"))
        assert cli.connect(start=True).port == srv.server_address[1]
        _Fake.locked = True
        with pytest.raises(cli.ApiError, match="locked"):
            cli.connect(start=False)
    finally:
        _Fake.locked = False
        srv.shutdown()


def test_connect_without_a_sidecar_and_no_start(monkeypatch):
    monkeypatch.setattr(cli, "_discover", lambda: None)
    with pytest.raises(cli.ApiError, match="not running"):
        cli.connect(start=False)


@pytest.mark.skipif(os.name == "nt", reason="the launcher is a bash script")
def test_install_and_remove_the_launcher(monkeypatch, tmp_path):
    path = str(tmp_path / "bin" / "raven")
    monkeypatch.setattr(cli, "LAUNCHER", path)
    st = cli.status()
    assert st["supported"] and not st["installed"] and st["features"][0]["id"] == "search"
    st = cli.install()
    assert st["installed"] and st["current"] and os.access(path, os.X_OK)
    assert "init bash" in st["setup"]["bash"]
    st = cli.remove()
    assert not st["installed"] and not os.path.exists(path)


@pytest.mark.skipif(os.name == "nt", reason="the launcher is a bash script")
def test_install_keeps_a_command_that_raven_did_not_write(monkeypatch, tmp_path):
    path = tmp_path / "raven"
    path.write_text("#!/bin/sh\necho other\n")
    monkeypatch.setattr(cli, "LAUNCHER", str(path))
    assert cli.status()["other"]
    with pytest.raises(cli.ApiError):
        cli.install()
    with pytest.raises(cli.ApiError):
        cli.remove()
    assert "other" in path.read_text()


@pytest.mark.skipif(os.name == "nt", reason="the launcher is a bash script")
def test_appimage_launcher_mounts_the_image(monkeypatch, tmp_path):
    import subprocess

    monkeypatch.setattr(cli, "FROZEN", True)
    monkeypatch.setenv("APPIMAGE", "/opt/Raven 1.AppImage")
    s = cli._launcher_script()
    assert "--appimage-mount" in s and "'/opt/Raven 1.AppImage'" in s and 'raven-core" cli "$@"' in s
    f = tmp_path / "raven"
    f.write_text(s)
    assert subprocess.run(["bash", "-n", str(f)]).returncode == 0
