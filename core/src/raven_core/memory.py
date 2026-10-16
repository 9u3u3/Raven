"""Give freed memory back to the system (PLAN2 P2-6). glibc keeps freed heap pages for the process. After a
burst of work (parsing documents, a model that unloads) one malloc_trim makes the idle size small again."""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import threading
import time

log = logging.getLogger("raven.memory")
MODEL_IDLE = float(os.environ.get("RAVEN_MODEL_IDLE", "300"))  # PLAN2 P2-6: a model unused for 5 minutes unloads

_last = {"ts": 0.0}
try:
    _libc = ctypes.CDLL("libc.so.6") if os.name == "posix" else None
except OSError:
    _libc = None


def trim(min_gap: float = 60.0) -> None:
    now = time.monotonic()
    if now - _last["ts"] < min_gap:
        return
    _last["ts"] = now
    gc.collect()
    if _libc is not None and hasattr(_libc, "malloc_trim"):
        _libc.malloc_trim(0)


def unload_when_idle(obj, name: str, attrs: tuple[str, ...] = ("_sess",), idle: float | None = None) -> None:
    """Drop `obj`'s model (its `attrs`) after `idle` seconds with no use. `obj` sets `_used` on each use and
    holds `_lock` while it runs the model; the next use loads the model again."""
    if getattr(obj, "_reaper_started", False):
        return
    obj._reaper_started = True
    wait = idle if idle is not None else MODEL_IDLE

    def run() -> None:
        while True:
            time.sleep(min(30.0, wait / 2))
            if getattr(obj, attrs[0], None) is None or time.monotonic() - getattr(obj, "_used", 0.0) < wait:
                continue
            with obj._lock:
                if time.monotonic() - getattr(obj, "_used", 0.0) < wait:
                    continue
                for a in attrs:
                    setattr(obj, a, None)
            trim(min_gap=0)
            log.info("%s unloaded after %.0f s with no use", name, wait)

    threading.Thread(target=run, name=f"{name}-unload", daemon=True).start()
