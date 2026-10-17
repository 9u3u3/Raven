"""Settings, Data (PLAN4 section 6): the folder parts, the settings export without keys, and the delete plan."""

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="raven-data-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import keyring  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402

from raven_core import config, data, db, keys  # noqa: E402

db.init()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.d = {}

    def get_password(self, s, u):
        return self.d.get((s, u))

    def set_password(self, s, u, p):
        self.d[(s, u)] = p

    def delete_password(self, s, u):
        self.d.pop((s, u), None)


@pytest.fixture(autouse=True)
def ring():
    """A keyring in memory: the tests must not write to the real keyring of the computer."""
    old = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(old)


def test_info_lists_the_parts():
    (config.THUMB_DIR / "a.webp").write_bytes(b"x" * 1000)
    r = data.info()
    assert r["dir"] == str(config.DATA_DIR)
    parts = {p["id"]: p["bytes"] for p in r["parts"]}
    assert parts["index"] > 0 and parts["thumbs"] >= 1000
    assert r["total"] >= sum(parts.values()) - parts.get("other", 0)


def test_export_has_no_keys():
    db.set_setting("power_user", True)
    keys.put("groq_api_key", "gsk_secret_value_1234")
    out = data.export_settings()
    text = repr(out)
    assert out["settings"]["power_user"] is True
    assert "gsk_secret" not in text and "groq_api_key" not in out["settings"]
    assert "ui_tabs" not in out["settings"]


def test_delete_plan_keeps_the_trash_and_a_models_folder_outside():
    from raven_core.platform import IS_LINUX, trash_home

    plan = data.delete_plan()
    assert all(os.path.dirname(p) == str(config.DATA_DIR) for p in plan["paths"])
    assert str(trash_home()) not in plan["paths"]
    if not IS_LINUX:
        assert plan["keeps"] == [str(trash_home())]
    if not data._inside(config.MODELS_DIR, config.DATA_DIR):
        assert str(config.MODELS_DIR) not in plan["paths"]


def test_delete_all_route_needs_the_word_and_forgets_keys(monkeypatch):
    from raven_core import app as app_mod

    monkeypatch.setattr(app_mod, "TOKEN", "test-token")
    monkeypatch.setattr(app_mod.indexer, "shutdown", lambda: None)  # later tests use the indexer
    keys.put("groq_api_key", "gsk_secret_value_1234")
    c = TestClient(app_mod.app, headers={"Authorization": "Bearer test-token"})
    assert c.post("/data/delete-all", json={"confirm": "yes"}).status_code == 400
    r = c.post("/data/delete-all", json={"confirm": "DELETE"})
    assert r.status_code == 200, r.text
    assert "groq_api_key" in r.json()["keys"] and keys.get("groq_api_key") is None
    assert os.path.isdir(config.DATA_DIR)  # the sidecar deletes nothing itself
