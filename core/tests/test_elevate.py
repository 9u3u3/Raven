"""The elevated helper (run here as the user, without pkexec): its checks and its operations."""

import json
import os
import subprocess
import sys

import pytest

HELPER = os.path.join(os.path.dirname(__file__), "..", "src", "raven_core", "fs", "elevate_helper.py")


def run(req):
    r = subprocess.run([sys.executable, HELPER, json.dumps(req)], capture_output=True, text=True)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def test_operations(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x")
    assert run({"op": "copy", "src": str(a), "dst": str(tmp_path / "b.txt")})[0] == 0
    assert run({"op": "rename", "src": str(tmp_path / "b.txt"), "dst": str(tmp_path / "c.txt")})[0] == 0
    assert run({"op": "mkdir", "dst": str(tmp_path / "d")})[0] == 0
    assert run({"op": "delete", "src": str(tmp_path / "c.txt")})[0] == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "d"]


@pytest.mark.skipif(os.name == "nt", reason="pkexec and POSIX system folders")
def test_refuses_system_and_relative_paths(tmp_path):
    code, out = run({"op": "delete", "src": "/etc"})
    assert code == 1 and "does not change" in out["error"]
    code, out = run({"op": "mkdir", "dst": "relative/dir"})
    assert code == 1 and "absolute" in out["error"]
    code, out = run({"op": "rename", "src": str(tmp_path), "dst": "/"})
    assert code == 1
