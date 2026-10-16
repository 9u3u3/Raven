"""Auto compression mode (section 9.8, step 3).

The user turns it on per folder and sets rules: minimum cold days, minimum size, a
reversible method and a daily limit. Raven runs only on AC power when the laptop is idle,
and waits 3 days after a file set first becomes a candidate (Sparkle-style buffer).
Each run is one journal group, so one undo reverses it. Lossy methods are never used here.
"""

from __future__ import annotations

import glob
import logging
import os
import threading
import time
import uuid

from .. import db, events
from ..fs import ops
from . import jobs

log = logging.getLogger("raven.auto")

METHODS = ("auto", "7z", "tar.zst", "zip")  # "auto": the candidate's own exact method (7z, lossless JPEG XL, zstd)
BUFFER_DAYS = 3
CHECK_EVERY = 30 * 60
_lock = threading.Lock()


def rules() -> list[dict]:
    return db.get_setting("auto_rules", []) or []


def set_rules(new: list[dict]) -> list[dict]:
    clean = []
    for r in new:
        folder = os.path.abspath(os.path.expanduser(str(r.get("folder", "")).strip()))
        if not folder or not os.path.isdir(folder):
            raise ValueError(f"Not a folder: {folder}")
        method = r.get("method") if r.get("method") in METHODS else "auto"
        clean.append({
            "id": r.get("id") or uuid.uuid4().hex[:8],
            "folder": folder,
            "cold_days": max(7, int(r.get("cold_days") or 90)),
            "min_mb": max(1, int(r.get("min_mb") or 50)),
            "method": method,
            "daily_gb": max(0.1, float(r.get("daily_gb") or 5)),
            "enabled": bool(r.get("enabled", True)),
        })
    db.set_setting("auto_rules", clean)
    return clean


def on_ac() -> bool:
    """True on mains power. A machine with no battery counts as on mains."""
    supplies = glob.glob("/sys/class/power_supply/*")
    mains = [s for s in supplies if _read(os.path.join(s, "type")) == "Mains"]
    if not mains:
        return not any(_read(os.path.join(s, "type")) == "Battery" for s in supplies)
    return any(_read(os.path.join(s, "online")) == "1" for s in mains)


def idle() -> bool:
    from ..index.indexer import indexer

    try:
        load = os.getloadavg()[0] / (os.cpu_count() or 1)
    except OSError:
        load = 0
    return load < 0.35 and not indexer.interactive.is_set()


def _read(p: str) -> str:
    try:
        with open(p) as f:
            return f.read().strip()
    except OSError:
        return ""


def _log(entry: dict) -> None:
    entries = db.get_setting("auto_log", []) or []
    entries.insert(0, entry)
    db.set_setting("auto_log", entries[:30])


def _scan(folder: str):
    from ..storage.scan import Scanner, store

    found = store.find(folder)
    if found:
        return found
    sc = Scanner(folder)
    res = sc.run()
    store.results[sc.root] = res
    return store.find(folder)


def run_rule(rule: dict, skip_wait: bool = False) -> dict:
    """Run one rule now. `skip_wait` skips the 3-day buffer (the "Run now" button)."""
    from .candidates import candidates, forget_cache

    with _lock:
        folder = rule["folder"]
        if not os.path.isdir(folder):
            return {"rule": rule["id"], "jobs": 0, "saved": 0, "note": "the folder is gone"}
        forget_cache()
        _scan(folder)
        rows = candidates(folder, cold_days=rule["cold_days"], limit=50)
        min_bytes = rule["min_mb"] * 1024 * 1024
        seen = db.get_setting("auto_seen", {}) or {}
        now = time.time()
        today = time.strftime("%Y-%m-%d")
        done_today = (db.get_setting("auto_today", {}) or {}).get(today, 0)
        limit = rule["daily_gb"] * 1024**3
        g = ops.Group("auto", f"Auto compression in {os.path.basename(folder)}")
        made, saved, waiting, errors = 0, 0, 0, []
        try:
            for row in rows:
                size = sum(i["size"] for i in row["items"])
                if size < min_bytes:
                    continue
                first = seen.setdefault(row["id"], now)
                if not skip_wait and now - first < BUFFER_DAYS * 86400:
                    waiting += 1
                    continue
                if done_today + size > limit:
                    break
                method = row["method"] if rule["method"] == "auto" or row["method"] in ("jxl", "zst") else rule["method"]
                try:
                    job = jobs.compress(row["root"], row["unit"], method, mode="auto", group=g)
                except Exception as e:  # noqa: BLE001 - one failed set must not stop the run
                    errors.append(f"{os.path.basename(row['unit'][0])}: {e}")
                    continue
                made += 1
                saved += (job.get("size_before") or 0) - (job.get("size_after") or 0)
                done_today += size
                seen.pop(row["id"], None)
        finally:
            g.commit()
        db.set_setting("auto_seen", seen)
        db.set_setting("auto_today", {today: done_today})
        entry = {"ts": int(now * 1000), "rule": rule["id"], "folder": folder, "jobs": made, "saved": saved,
                 "waiting": waiting, "errors": errors[:3], "group": g.id if made else None}
        _log(entry)
        if made:
            from ..storage.scan import store

            store.refresh_paths([folder])
            forget_cache()
            events.publish("auto_compress", **entry, label=g.label)
        return entry


def loop(stop: threading.Event) -> None:
    """Check the rules every 30 minutes. Act only on AC power when the laptop is idle."""
    stop.wait(120)
    while not stop.is_set():
        try:
            active = [r for r in rules() if r.get("enabled")]
            if active and on_ac() and idle():
                for r in active:
                    run_rule(r)
        except Exception:  # noqa: BLE001
            log.exception("auto compression failed")
        stop.wait(CHECK_EVERY)
