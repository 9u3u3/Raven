"""Cloud providers without network: the Anthropic API answer parsing, the key test, and key masking."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))
os.environ.setdefault("RAVEN_TOKEN", "test-token")

import httpx  # noqa: E402
import pytest  # noqa: E402

from raven_core import db  # noqa: E402
from raven_core.router import cloud  # noqa: E402

db.init()


class Resp:
    def __init__(self, code, data):
        self.status_code, self._d, self.text = code, data, str(data)

    def json(self):
        return self._d


def test_anthropic_chat_reads_the_tool_answer(monkeypatch):
    db.set_setting("anthropic_api_key", "sk-ant-test")
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return Resp(200, {"content": [{"type": "text", "text": "x"}, {"type": "tool_use", "name": "answer", "input": {"items": [1]}}]})

    monkeypatch.setattr(cloud.httpx, "post", fake_post)
    data, model = cloud.anthropic_chat("sys", "user", {"type": "object"})
    assert data == {"items": [1]} and model.startswith("Anthropic")
    assert sent["headers"]["x-api-key"] == "sk-ant-test" and sent["body"]["tool_choice"]["name"] == "answer"
    assert cloud.configured()["anthropic"]


def test_anthropic_error_is_a_cloud_error(monkeypatch):
    db.set_setting("anthropic_api_key", "sk-ant-test")
    monkeypatch.setattr(cloud.httpx, "post", lambda *a, **k: Resp(401, {"error": "invalid x-api-key"}))
    with pytest.raises(cloud.CloudError):
        cloud.anthropic_chat("sys", "user", {"type": "object"})


def test_key_test_reports_a_bad_key(monkeypatch):
    db.set_setting("anthropic_api_key", "sk-ant-bad")
    monkeypatch.setattr(cloud.httpx, "get", lambda *a, **k: Resp(401, {}))
    assert cloud.test_key("anthropic") == {"ok": False, "detail": "The key is not valid"}
    db.set_setting("anthropic_api_key", None)
    assert cloud.test_key("anthropic")["detail"] == "No key"


def test_settings_never_return_a_key():
    from fastapi.testclient import TestClient

    from raven_core.app import app

    db.set_setting("anthropic_api_key", "sk-ant-secret-1234")
    from raven_core import app as app_mod

    # No `with`: the lifespan (indexer threads) does not start, so other tests keep a quiet database.
    r = TestClient(app).get("/settings", headers={"Authorization": f"Bearer {app_mod.TOKEN}"})
    assert r.status_code == 200, r.text
    assert r.json()["anthropic_api_key"] == "…1234"
    assert httpx is not None


def test_claude_env_picks_the_account(monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "x")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/somewhere/else")
    db.set_setting("claude_config_dir", "~/.claude-other")
    env = cloud.claude_env()
    assert env["CLAUDE_CONFIG_DIR"] == os.path.expanduser("~/.claude-other")
    assert "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    # The default account: no CLAUDE_CONFIG_DIR at all (its config is ~/.claude.json).
    db.set_setting("claude_config_dir", "")
    assert "CLAUDE_CONFIG_DIR" not in cloud.claude_env()


def test_claude_env_uses_the_real_sign_in_when_home_is_a_demo_folder(tmp_path, monkeypatch):
    """With HOME set to a demo folder that has no Claude sign-in, claude -p uses the one in the real home."""
    import pwd

    from raven_core.router import cloud

    real = tmp_path / "real"
    (real / ".claude").mkdir(parents=True)
    (real / ".claude" / ".credentials.json").write_text("{}")
    demo = tmp_path / "demo"
    demo.mkdir()
    monkeypatch.setenv("HOME", str(demo))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: type("P", (), {"pw_dir": str(real)})())
    monkeypatch.setattr(cloud.db, "get_setting", lambda k, default=None: None)
    assert cloud.claude_env()["CLAUDE_CONFIG_DIR"] == str(real / ".claude")
    # A demo home with its own sign-in (or links to the real one) keeps it.
    (demo / ".claude").mkdir()
    (demo / ".claude" / ".credentials.json").write_text("{}")
    assert "CLAUDE_CONFIG_DIR" not in cloud.claude_env()
    # A chosen account always wins.
    assert cloud.claude_env("/x/acct")["CLAUDE_CONFIG_DIR"] == "/x/acct"
