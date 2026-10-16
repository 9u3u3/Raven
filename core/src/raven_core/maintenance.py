"""Index upkeep when the laptop is idle and on power (PLAN3 P0-8).

- An index from an older version has no auto-vacuum. When more than COMPACT_SHARE of it is free pages, one
  full compact() rebuilds it and switches it to incremental auto-vacuum. At most once a day.
- An incremental index gives up to 100 MB of free pages back on each run (db.trim()).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time

from . import db

log = logging.getLogger("raven.maintenance")

EVERY = 30 * 60
COMPACT_SHARE = 0.10
COMPACT_GAP = 24 * 3600


def disk_room_for_compact(info: dict) -> bool:
    """VACUUM writes a full copy through the WAL: it needs free space of about the size of the index."""
    free = shutil.disk_usage(db.DB_PATH.parent).free
    return free > info["bytes"] * 1.2 + 512 * 1024**2


def run_once(force: bool = False) -> dict:
    info = db.size_info()
    if info["auto_vacuum"] != "incremental":
        last = db.get_setting("index_compacted_at", 0) or 0
        if (force or (info["free_share"] > COMPACT_SHARE and time.time() - last > COMPACT_GAP)) and disk_room_for_compact(info):
            out = db.compact()
            db.set_setting("index_compacted_at", int(time.time()))
            log.info("index compact: %d MB freed", out["freed_bytes"] // 1024**2)
            return {"action": "compact", **out}
        return {"action": "none", "before": info, "after": info, "freed_bytes": 0}
    if force:
        out = db.compact()
        db.set_setting("index_compacted_at", int(time.time()))
        return {"action": "compact", **out}
    pages = db.trim()
    after = db.size_info()
    return {"action": "trim", "before": info, "after": after, "freed_bytes": max(0, info["bytes"] - after["bytes"]),
            "pages": pages}


def loop(stop: threading.Event) -> None:
    from .compress.auto import idle, on_ac

    while not stop.wait(EVERY):
        try:
            if on_ac() and idle():
                run_once()
        except Exception:  # noqa: BLE001  (a busy or locked index: the next run tries again)
            log.exception("index maintenance failed")
