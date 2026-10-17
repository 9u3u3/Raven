"""Lite mode (PLAN2 P2-6): Raven on a computer with little RAM, no GPU or few CPU cores.

Full  every AI feature, Ollama for meaning and the local model.
Lite  no Ollama: a static embedding model on the CPU (index/embed.py), no reranker, no image or speech
      models unless the user turns them on, Privacy Filter only for cloud payloads, one extraction worker,
      a low CPU and I/O priority, and a pause on battery.

Each feature also has its own switch (setting "features"), so a user can mix the two modes.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import db

log = logging.getLogger("raven.lite")

FEATURES = {  # name: (label, on in full, on in lite)
    "llm": ("Local AI model (Ask, summaries, plans)", True, False),
    "reranker": ("Better result order (reranker)", True, False),
    "image": ("Find photos by what they show", True, False),
    "speech": ("Search what people say", True, False),
    "pii_index": ("Privacy Filter while indexing", True, False),
    # Runs only on cloud payloads, so lite mode keeps it on. It needs its model (features.py "strict").
    "pii_strict": ("Strict privacy: a second model before each cloud call", True, True),
    "galaxy": ("File map by meaning", True, False),
}
LITE_EMBED = "potion-retrieval-32m"
LITE_EMBED_SMALL = "potion-base-8m"


def hardware() -> dict:
    """RAM, GPU memory, CPU threads, free disk and battery."""
    out: dict = {"threads": os.cpu_count() or 1, "ram": 0, "ram_free": 0, "vram": 0, "gpu": None, "disk_free": 0,
                 "battery": None, "on_battery": False}
    try:
        info = Path("/proc/meminfo").read_text()
        kv = {ln.split(":")[0]: int(ln.split()[1]) * 1024 for ln in info.splitlines() if ln.split()[1:2]}
        out["ram"], out["ram_free"] = kv.get("MemTotal", 0), kv.get("MemAvailable", 0)
    except (OSError, ValueError, IndexError):
        try:
            import psutil  # type: ignore[import-not-found]

            vm = psutil.virtual_memory()
            out["ram"], out["ram_free"] = vm.total, vm.available
        except Exception:  # noqa: BLE001
            pass
    if shutil.which("nvidia-smi"):
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                name, mem = r.stdout.strip().splitlines()[0].rsplit(",", 1)
                out["gpu"], out["vram"] = name.strip(), int(float(mem)) * 1024 * 1024
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    try:
        out["disk_free"] = shutil.disk_usage(Path.home()).free
    except OSError:
        pass
    out.update(_battery())
    return out


def _battery() -> dict:
    base = Path("/sys/class/power_supply")
    pct, on_battery = None, False
    try:
        for d in base.iterdir():
            kind = (d / "type").read_text().strip() if (d / "type").exists() else ""
            if kind == "Battery" and (d / "capacity").exists():
                pct = int((d / "capacity").read_text().strip())
                status = (d / "status").read_text().strip() if (d / "status").exists() else ""
                on_battery = on_battery or status == "Discharging"
            elif kind == "Mains" and (d / "online").exists() and (d / "online").read_text().strip() == "0":
                on_battery = True
    except OSError:
        pass
    return {"battery": pct, "on_battery": on_battery}


def suggest(hw: dict | None = None) -> str:
    hw = hw or hardware()
    gb = 1024**3
    if hw["ram"] >= 12 * gb and (hw["vram"] >= 4 * gb or hw["threads"] >= 8):
        return "full"
    return "lite"


def lite_model(hw: dict | None = None) -> str:
    """The static model for lite mode. potion-retrieval-32m finds more (MRR 0.967 on testdata), but it holds
    about 90 MB more while it is loaded. Under 8 GB of RAM, potion-base-8m (MRR 0.900) keeps the sidecar
    under 300 MB (testdata/lite_eval.py)."""
    hw = hw or hardware()
    return LITE_EMBED if hw["ram"] >= 8 * 1024**3 else LITE_EMBED_SMALL


def mode() -> str:
    return db.get_setting("mode") or "full"


def enabled(feature: str) -> bool:
    """A feature's switch: the user's choice, else the default of the mode."""
    try:
        own = (db.get_setting("features") or {}).get(feature)
    except Exception:  # noqa: BLE001 - before the database opens
        own = None
    if own is not None:
        return bool(own)
    label, full, lite = FEATURES.get(feature, ("", True, True))
    return full if mode() == "full" else lite


def set_feature(feature: str, on: bool | None) -> None:
    if feature not in FEATURES:
        raise ValueError(f"Unknown feature {feature}")
    f = dict(db.get_setting("features") or {})
    if on is None:
        f.pop(feature, None)
    else:
        f[feature] = bool(on)
    db.set_setting("features", f)


def set_mode(new: str) -> dict:
    """Switch the mode. The embedding model changes with it, so the index embeds again in the background
    (search uses names and keywords until it ends)."""
    if new not in ("full", "lite"):
        raise ValueError("The mode must be full or lite")
    from . import config
    from .index import embed

    db.set_setting("mode", new)
    if new == "full":
        want = db.get_setting("full_embed_model") or config.EMBED_MODEL
    else:
        cur = db.get_setting("embed_model")
        if cur and not cur.startswith("potion"):
            db.set_setting("full_embed_model", cur)  # back to it when the user leaves lite mode
        want = lite_model()
        if not embed.static_available(want):
            from . import models

            models.download([want])
    changed = (db.get_setting("embed_model") or config.EMBED_MODEL) != want
    db.set_setting("embed_model", want)
    apply_priority()
    return {"mode": new, "embed_model": want, "reembed": changed}


def status() -> dict:
    from .index import embed

    hw = hardware()
    return {
        "mode": mode(), "suggested": suggest(hw), "hardware": hw,
        "features": {k: {"label": v[0], "on": enabled(k)} for k, v in FEATURES.items()},
        "embed_model": embed.model(), "lite_model": lite_model(hw), "static_ready": embed.static_available(LITE_EMBED) or embed.static_available(LITE_EMBED_SMALL),
        "paused": paused(),
    }


_prio = {"done": None}


def apply_priority() -> None:
    """Lite: a low CPU priority (nice 10) and idle I/O priority for the sidecar. The window stays quick,
    because it is another process."""
    want = mode() == "lite"
    if _prio["done"] == want or os.name == "nt":
        return
    try:
        if want:
            os.nice(10)
            if shutil.which("ionice"):
                subprocess.run(["ionice", "-c", "3", "-p", str(os.getpid())], capture_output=True, timeout=5)
        # A process cannot raise its own priority back without root: full mode takes effect at the next start.
    except OSError as e:
        log.info("priority: %s", e)
    _prio["done"] = want


_bat = {"ts": 0.0, "on": False}


def paused() -> bool:
    """Lite on battery: background indexing waits. Checked at most every 30 s."""
    if mode() != "lite":
        return False
    now = time.monotonic()
    if now - _bat["ts"] > 30:
        _bat.update(ts=now, on=_battery()["on_battery"])
    return _bat["on"]
