"""Raven as an MCP server: the gate side (mcp_gate.py)."""

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from fastapi import HTTPException  # noqa: E402

from raven_core import db, mcp_gate  # noqa: E402

db.init()


@pytest.fixture()
def root(tmp_path):
    old = db.get_setting("index_roots", [])
    db.set_setting("index_roots", [str(tmp_path)])
    db.set_setting("mcp_enabled", True)
    yield tmp_path
    db.set_setting("index_roots", old)
    db.set_setting("mcp_enabled", False)


def test_off_by_default():
    db.set_setting("mcp_enabled", False)
    with pytest.raises(HTTPException) as e:
        mcp_gate._need()
    assert e.value.status_code == 403


def test_only_indexed_folders(root):
    with pytest.raises(HTTPException):
        mcp_gate._in("/etc")
    assert mcp_gate._in(str(root / "x")) == str(root / "x")


def test_read_is_redacted_and_logged(root):
    p = root / "note.txt"
    p.write_text("Contact rahul.sharma@example.com or 9876543210. The rent is 18,000.")
    n = db.conn().execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
    out = mcp_gate.read(str(p), 2000)
    assert "rahul.sharma@example.com" not in out["text"] and "9876543210" not in out["text"]
    assert "[EMAIL_1]" in out["text"] and "18,000" in out["text"]
    row = db.conn().execute("SELECT feature, payload, pii_count FROM ledger ORDER BY id DESC LIMIT 1").fetchone()
    assert db.conn().execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == n + 1
    assert row["feature"] == "mcp read" and row["pii_count"] >= 2 and "example.com" not in row["payload"]


def test_list_folder_hides_home(root):
    (root / "a.pdf").write_text("x")
    out = mcp_gate.list_folder(str(root))
    assert [i["name"] for i in out["items"]] == ["a.pdf"]
    assert not out["path"].startswith(os.path.expanduser("~") + "/")
