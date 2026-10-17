"""MCP client buttons (PLAN4 R4): add Raven to a client's config file and keep every other entry. All files live
in a temporary home folder."""

import json
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")

import pytest  # noqa: E402

from raven_core import mcp_clients  # noqa: E402

ENTRY = {"command": "/opt/raven/raven-core", "args": ["--mcp"]}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setattr(mcp_clients, "_claude_cli", lambda: None)
    return tmp_path


def test_add_keeps_other_servers_and_makes_a_backup(home):
    f = home / ".config" / "Claude" / "claude_desktop_config.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    st = {c["id"]: c for c in mcp_clients.status(ENTRY)}
    assert st["claude_desktop"]["found"] and not st["claude_desktop"]["added"]
    assert not st["cursor"]["found"]
    mcp_clients.add("claude_desktop", ENTRY)
    data = json.loads(f.read_text())
    assert data["mcpServers"] == {"other": {"command": "x"}, "raven": ENTRY} and data["theme"] == "dark"
    assert json.loads((f.parent / "claude_desktop_config.json.raven-backup").read_text())["mcpServers"] == {"other": {"command": "x"}}
    st = {c["id"]: c for c in mcp_clients.status(ENTRY)}
    assert st["claude_desktop"]["added"] and st["claude_desktop"]["current"]
    mcp_clients.remove("claude_desktop")
    assert json.loads(f.read_text())["mcpServers"] == {"other": {"command": "x"}}


def test_vscode_uses_its_own_shape(home):
    mcp_clients.add("vscode", ENTRY)
    data = json.loads((home / ".config" / "Code" / "User" / "mcp.json").read_text())
    assert data["servers"]["raven"] == {"type": "stdio", **ENTRY}


def test_a_broken_file_stays_as_it_is(home):
    f = home / ".cursor" / "mcp.json"
    f.parent.mkdir(parents=True)
    f.write_text("{ not json")
    with pytest.raises(mcp_clients.ClientError, match="not valid JSON"):
        mcp_clients.add("cursor", ENTRY)
    assert f.read_text() == "{ not json"


def test_claude_code_needs_its_command(home):
    with pytest.raises(mcp_clients.ClientError, match="not installed"):
        mcp_clients.add("claude_code", ENTRY)
