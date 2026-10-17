"""A release build has no `claude -p` and no developer paths (PLAN4 R1, B1). A log file keeps the errors (B5)."""

import logging
import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

from fastapi import HTTPException  # noqa: E402

from raven_core import app as app_mod  # noqa: E402
from raven_core import config, db, keys, logs, notices  # noqa: E402
from raven_core.router import cloud, providers  # noqa: E402
from raven_core.router import routes as router_routes  # noqa: E402

db.init()


@pytest.fixture
def release(monkeypatch):
    monkeypatch.setattr(config, "DEV", False)
    monkeypatch.setattr(cloud.shutil, "which", lambda name: f"/usr/bin/{name}")  # a claude CLI is on PATH


def test_a_release_build_never_offers_claude_p(release):
    assert cloud.claude_ok() is False
    assert cloud.configured().get("claude", False) is False
    assert "claude" not in providers.presets()
    assert cloud.claude_accounts() == []
    assert router_routes.status()["dev"] is False
    assert app_mod.health()["dev"] is False


def test_a_release_build_refuses_the_claude_settings(release):
    for key, value in (("cloud_provider", "claude"), ("claude_model", "opus"), ("claude_config_dir", "~/.claude-x")):
        with pytest.raises(HTTPException) as e:
            app_mod.set_setting(app_mod.SettingBody(key=key, value=value))
        assert e.value.status_code == 400
    with pytest.raises(providers.ProviderError):
        providers.add("claude")


def test_a_development_build_keeps_claude_p(monkeypatch):
    monkeypatch.setattr(config, "DEV", True)
    monkeypatch.setattr(cloud.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert cloud.claude_ok() is True
    providers.add("claude")
    assert providers.order() == ["claude"]
    providers.remove("claude")


def test_the_log_file_cuts_long_messages(tmp_path, monkeypatch):
    monkeypatch.setattr(logs, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logs, "LOG_FILE", tmp_path / "raven-core.log")
    monkeypatch.setattr(logs, "CRASH_FILE", tmp_path / "crash.log")
    logs.setup()
    logging.getLogger("raven.test").warning("x" * 10_000)
    for h in logging.getLogger().handlers:
        h.flush()
    text = (tmp_path / "raven-core.log").read_text()
    assert "(cut)" in text
    assert max(len(ln) for ln in text.splitlines()) < logs.MAX_MESSAGE + 200
    if os.name != "nt":
        assert (tmp_path / "raven-core.log").stat().st_mode & 0o077 == 0


def test_diagnostics_hold_no_key_and_no_home_folder(monkeypatch):
    secret = "gsk_" + "A1b2C3d4" * 6
    monkeypatch.setattr(keys, "get", lambda name: secret if name == "groq_api_key" else None)
    db.set_setting("theme", "dark")
    text = logs.diagnostics(lines=20)
    assert secret not in text and secret[-4:] not in text
    assert "keys stored: groq_api_key" in text
    assert "theme" in text
    home = os.path.expanduser("~")
    assert home not in text


def test_the_notices_list_the_models_and_libraries():
    n = notices.load()
    names = {m["name"] for m in n["models"]}
    assert any("EmbeddingGemma" in x for x in names)
    gemma = next(m for m in n["models"] if "Gemma" in m["name"])
    assert "Gemma Terms of Use" in gemma["notice"]
    assert any(p["name"].lower() == "fastapi" for p in n["python"])
    assert any(p["name"] == "react" for p in n["node"])


def test_one_version_in_all_places():
    """config.py, pyproject.toml, package.json and the changelog name the same version (CHANGELOG.md)."""
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    py = re.search(r'^version = "([^"]+)"', (root / "core/pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    js = json.loads((root / "app/package.json").read_text(encoding="utf-8"))["version"]
    assert config.VERSION == py == js
    assert f"## {config.VERSION}" in notices.changelog()
