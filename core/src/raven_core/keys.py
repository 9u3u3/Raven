"""Cloud API keys (PLAN3 P0-3): the system keyring when one runs, else the settings table.

The settings table lives in the index, and a plain index is a normal SQLite file. The keyring (Secret Service on
Linux, the Credential Manager on Windows, the Keychain on macOS) keeps the keys out of that file. Without a
keyring, the keys stay in the database: the file has mode 0600 in a 0700 folder (PLAN3 P0-2), and Settings says
where the keys are.
"""

from __future__ import annotations

import hashlib
import logging

from . import db
from .config import DATA_DIR

log = logging.getLogger("raven.keys")

NAMES = ("groq_api_key", "anthropic_api_key")
SERVICE = "raven"


def _account(name: str) -> str:
    # One entry for each data folder, as secure.py does: a test or demo data folder has its own keys.
    return f"{name}:" + hashlib.sha256(str(DATA_DIR).encode()).hexdigest()[:12]


def _keyring():
    from .secure import keyring_ok

    if not keyring_ok():
        return None
    import keyring

    return keyring


def store() -> str:
    """Where new keys go: "keyring" or "database"."""
    return "keyring" if _keyring() else "database"


def get(name: str) -> str | None:
    kr = _keyring()
    if kr:
        try:
            v = kr.get_password(SERVICE, _account(name))
            if v:
                return v
        except Exception:  # noqa: BLE001  (a locked or broken keyring: use the database copy, if any)
            log.warning("keyring read failed for %s", name)
    return db.get_setting(name) or None


def put(name: str, value: str | None) -> str:
    """Store or delete one key. Returns where it went."""
    kr = _keyring()
    if kr:
        try:
            if value:
                kr.set_password(SERVICE, _account(name), value)
            else:
                try:
                    kr.delete_password(SERVICE, _account(name))
                except Exception:  # noqa: BLE001  (no entry to delete)
                    pass
            _drop_db_copy(name)
            return "keyring"
        except Exception:  # noqa: BLE001
            log.warning("keyring write failed for %s: the key stays in the database", name)
    db.set_setting(name, value or None)
    return "database"


def _drop_db_copy(name: str) -> None:
    # secure_delete: SQLite writes zeros over the old row, so the key does not stay in a free page.
    db.conn().execute("PRAGMA secure_delete=ON")
    with db.tx() as c:
        c.execute("DELETE FROM settings WHERE key=?", (name,))


def migrate() -> int:
    """Move the keys that older versions stored in the database into the keyring. Returns the count."""
    if not _keyring():
        return 0
    moved = 0
    for name in NAMES:
        v = db.get_setting(name)
        if v and put(name, v) == "keyring":
            moved += 1
    if moved:
        log.info("moved %d cloud key(s) from the database to the system keyring", moved)
    return moved
