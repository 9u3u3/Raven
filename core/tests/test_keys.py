"""Cloud keys go to the system keyring when one runs (PLAN3 P0-3)."""

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="raven-test-")
os.environ.setdefault("RAVEN_DATA_DIR", os.path.join(_tmp, "data"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_tmp, "xdg"))

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.backends import fail  # noqa: E402

from raven_core import db, keys  # noqa: E402
from raven_core.router import cloud  # noqa: E402

db.init()


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        self.items.pop((service, username), None)


def _db_has(name: str) -> bool:
    return db.conn().execute("SELECT 1 FROM settings WHERE key=?", (name,)).fetchone() is not None


@pytest.fixture()
def ring():
    old = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(old)
    for n in keys.NAMES:
        db.set_setting(n, None)


@pytest.fixture()
def no_ring():
    old = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    yield
    keyring.set_keyring(old)
    for n in keys.NAMES:
        db.set_setting(n, None)


def test_key_goes_to_the_keyring_not_the_database(ring, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert keys.put("groq_api_key", "gsk_test_1234") == "keyring"
    assert not _db_has("groq_api_key")
    assert list(ring.items.values()) == ["gsk_test_1234"]
    from raven_core.router import providers

    assert providers.key("groq") == "gsk_test_1234"
    assert keys.store() == "keyring"


def test_delete_removes_the_key(ring):
    keys.put("anthropic_api_key", "sk-ant-x")
    keys.put("anthropic_api_key", None)
    assert keys.get("anthropic_api_key") is None
    assert not ring.items


def test_old_database_keys_move_to_the_keyring(ring):
    db.set_setting("anthropic_api_key", "sk-ant-old-9999")
    assert keys.migrate() == 1
    assert not _db_has("anthropic_api_key")
    assert keys.get("anthropic_api_key") == "sk-ant-old-9999"


def test_without_a_keyring_the_database_keeps_the_key(no_ring):
    assert keys.store() == "database"
    assert keys.put("groq_api_key", "gsk_db") == "database"
    assert db.get_setting("groq_api_key") == "gsk_db"
    assert keys.migrate() == 0
    assert keys.get("groq_api_key") == "gsk_db"


def test_settings_route_shows_a_hint_and_the_store(ring):
    from fastapi.testclient import TestClient

    from raven_core import app as app_mod

    keys.put("anthropic_api_key", "sk-ant-secret-5678")
    h = {"Authorization": f"Bearer {app_mod.TOKEN}"}
    out = TestClient(app_mod.app).get("/settings", headers=h).json()
    assert out["anthropic_api_key"] == "…5678"
    assert out["key_store"] == "keyring"
    c = TestClient(app_mod.app, headers=h)
    c.post("/cloud/providers/add", json={"preset": "groq"})
    r = c.post("/cloud/providers/update", json={"id": "groq", "key": " gsk_new "})
    assert r.json()["key_store"] == "keyring"
    assert keys.get("groq_api_key") == "gsk_new"
