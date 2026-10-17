"""Encrypted index (section 13.12): SQLCipher with a passphrase.

The whole database is encrypted: file names, text chunks, vectors, PII spans, the journal, the privacy
ledger and the settings. SQLCipher derives the key from the passphrase (PBKDF2-HMAC-SHA512). Raven never
writes the passphrase to disk. With "remember on this computer", the system keyring keeps it (Secret
Service, Windows Credential Manager, macOS Keychain), and Raven opens the index at start without a prompt.
Without it, the sidecar starts locked, and every route except /health and /security answers 423.

Turning encryption on or off copies the database with sqlcipher_export, and the sidecar restarts to swap
the files before any connection opens. Thumbnails and video frames are caches outside the database:
Raven deletes them when encryption turns on, and they come back as you browse.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

import sqlite_vec

from .config import DATA_DIR, DB_PATH, THUMB_DIR

log = logging.getLogger("raven.secure")

MARKER = DATA_DIR / "encryption.json"
SERVICE = "raven"
MIN_LEN = 8

_key: dict[str, str | None] = {"pass": None}


def _marker() -> dict:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False}


def _write_marker(m: dict) -> None:
    tmp = MARKER.with_suffix(".tmp")
    tmp.write_text(json.dumps(m), encoding="utf-8")
    os.replace(tmp, MARKER)


def enabled() -> bool:
    return bool(_marker().get("enabled"))


def locked() -> bool:
    return enabled() and _key["pass"] is None


def passphrase() -> str | None:
    return _key["pass"]


def _account() -> str:
    return "index:" + hashlib.sha256(str(DATA_DIR).encode()).hexdigest()[:12]


def keyring_ok() -> bool:
    try:
        import keyring
        from keyring.backends import fail

        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:  # noqa: BLE001
        return False


def _remembered() -> str | None:
    if not keyring_ok():
        return None
    try:
        import keyring

        return keyring.get_password(SERVICE, _account())
    except Exception:  # noqa: BLE001
        return None


def _remember(p: str | None) -> None:
    if not keyring_ok():
        return
    import keyring

    try:
        if p is None:
            keyring.delete_password(SERVICE, _account())
        else:
            keyring.set_password(SERVICE, _account(), p)
    except Exception:  # noqa: BLE001
        log.warning("keyring write failed")


def _cipher_connect(path: Path | str, pw: str | None):
    import sqlcipher3

    c = sqlcipher3.connect(str(path), timeout=30, check_same_thread=False)
    if pw is not None:
        # PRAGMA key takes no bound parameters: quote the value.
        c.execute(f"PRAGMA key = '{_quote(pw)}'")
    c.enable_load_extension(True)
    c.load_extension(sqlite_vec.loadable_path())
    c.enable_load_extension(False)
    return c


def _quote(s: str) -> str:
    return s.replace("'", "''")


def check(pw: str) -> bool:
    """True when the passphrase opens the database."""
    try:
        c = _cipher_connect(DB_PATH, pw)
        c.execute("SELECT count(*) FROM sqlite_master").fetchone()
        c.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def startup() -> str:
    """Before any connection: finish a pending swap, then find the key. Returns "plain", "open" or "locked"."""
    m = _marker()
    swap = m.get("swap")
    if swap and Path(swap).exists():
        for suffix in ("", "-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(swap, DB_PATH)
        m.pop("swap", None)
        _write_marker(m)
        log.info("database swapped (%s)", "encrypted" if m.get("enabled") else "plain")
    if not m.get("enabled"):
        return "plain"
    pw = _remembered()
    if pw and check(pw):
        _key["pass"] = pw
        return "open"
    return "locked"


def unlock(pw: str) -> bool:
    if not enabled():
        return True
    if not check(pw):
        return False
    _key["pass"] = pw
    return True


def _checkpoint() -> None:
    from . import db

    try:
        db.conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001
        pass


def encrypt(pw: str, remember: bool) -> dict:
    """Copy the plain database into an encrypted one. The swap happens at the next start."""
    if enabled():
        raise ValueError("The index is encrypted already")
    if len(pw) < MIN_LEN:
        raise ValueError(f"The passphrase needs at least {MIN_LEN} characters")
    _checkpoint()
    out = DB_PATH.with_name("raven.db.encrypting")
    out.unlink(missing_ok=True)
    c = _cipher_connect(DB_PATH, None)  # no key: SQLCipher reads a plain database
    c.execute(f"ATTACH DATABASE '{_quote(str(out))}' AS enc KEY '{_quote(pw)}'")
    c.execute("SELECT sqlcipher_export('enc')")
    c.execute("DETACH DATABASE enc")
    c.close()
    _write_marker({"enabled": True, "swap": str(out)})
    _remember(pw if remember else None)
    # Thumbnails and frames are pictures of private files outside the database.
    shutil.rmtree(THUMB_DIR, ignore_errors=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    return {"restart": True}


def decrypt(pw: str) -> dict:
    if not enabled():
        raise ValueError("The index is not encrypted")
    if not check(pw):
        raise PermissionError("Wrong passphrase")
    _checkpoint()
    out = DB_PATH.with_name("raven.db.decrypting")
    out.unlink(missing_ok=True)
    c = _cipher_connect(DB_PATH, pw)
    c.execute(f"ATTACH DATABASE '{_quote(str(out))}' AS plain KEY ''")
    c.execute("SELECT sqlcipher_export('plain')")
    c.execute("DETACH DATABASE plain")
    c.close()
    _write_marker({"enabled": False, "swap": str(out)})
    _remember(None)
    return {"restart": True}


def status() -> dict:
    return {"enabled": enabled(), "locked": locked(), "keyring": keyring_ok(), "remembered": bool(_remembered()) if enabled() else False}


def plain_connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
