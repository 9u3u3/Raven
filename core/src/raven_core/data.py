"""Settings, Data (PLAN4 section 6 item 7): the data folder, its parts, the settings export, and "Delete all
Raven data".

The sidecar cannot delete its own data folder: on Windows an open database file cannot be deleted. So
`forget_secrets()` deletes the keys from the keyring, and the main process stops the sidecar and deletes the
folders in `delete_plan()`. The Trash folder stays, because on Windows and macOS it holds the user's trashed
files (platform.trash_home).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import config, db

# Settings that the export leaves out: window state, caches and secrets.
_NOT_EXPORTED = {"ui_tabs", "galaxy", "update_last", "first_run_done"}


def _size(path: Path) -> int:
    total = 0
    try:
        if path.is_file():
            return path.stat().st_size
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def info() -> dict:
    """The data folder and the size of each part."""
    from . import logs, ollama_setup
    from .platform import trash_home

    d = config.DATA_DIR
    parts = [
        ("index", "Index", sum(_size(p) for p in d.glob("raven.db*"))),
        ("thumbs", "Thumbnails", _size(config.THUMB_DIR)),
        ("logs", "Log files", _size(logs.LOG_DIR)),
    ]
    if _inside(config.MODELS_DIR, d):
        parts.append(("models", "Models", _size(config.MODELS_DIR)))
    if ollama_setup.HOME.exists():
        parts.append(("ollama", "Ollama, installed by Raven", _size(ollama_setup.HOME)))
    trash = trash_home()
    if _inside(trash, d) and trash.exists():
        parts.append(("trash", "Trash", _size(trash)))
    known = sum(p[2] for p in parts)
    total = _size(d)
    if total > known:
        parts.append(("other", "Other", total - known))
    return {
        "dir": str(d),
        "models_dir": str(config.MODELS_DIR),
        "models_inside": _inside(config.MODELS_DIR, d),
        "total": total,
        "parts": [{"id": i, "label": label, "bytes": b} for i, label, b in parts],
    }


def export_settings() -> dict:
    """The settings as one JSON object, without keys. The provider rows keep their order and models."""
    from . import keys
    from .router import providers

    secret = set(keys.names()) | set(keys.NAMES)
    out: dict = {}
    for k, v in db.conn().execute("SELECT key, value FROM settings ORDER BY key").fetchall():
        if k in _NOT_EXPORTED or k in secret or k.endswith("_api_key"):
            continue
        try:
            out[k] = json.loads(v)
        except (TypeError, ValueError):
            out[k] = v
    out["cloud_providers"] = [{k: r[k] for k in ("id", "preset", "name", "base_url", "model", "on") if k in r} for r in providers.rows()]
    return {"raven": config.VERSION, "settings": out}


def forget_secrets() -> list[str]:
    """Delete every key that Raven put in the system keyring for this data folder. Returns the names."""
    from . import keys, secure

    gone = []
    for name in keys.names():
        if keys.get(name):
            keys.put(name, None)
            gone.append(name)
    if secure._remembered():
        secure._remember(None)
        gone.append("index passphrase")
    return gone


def delete_plan() -> dict:
    """What the main process deletes after the sidecar stops: each entry of the data folder, without the Trash.
    A models folder outside the data folder (the repo `models/` in a development build) stays."""
    from .platform import trash_home

    d = config.DATA_DIR
    trash = trash_home()
    paths = [str(p) for p in sorted(d.iterdir()) if not (_inside(trash, d) and p.resolve() == trash.resolve())]
    return {"dir": str(d), "paths": paths, "keeps": [str(trash)] if _inside(trash, d) else []}
