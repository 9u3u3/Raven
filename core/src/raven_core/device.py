"""The device check (PLAN4 section 5.1): what this computer has, on Linux, macOS and Windows.

info() gives the system, the CPU, the RAM, the GPUs, the free disk and the battery. psutil reads the
RAM, the CPU and the battery on all three systems. The GPU check uses the tools of each system: nvidia-smi
and /sys/class/drm on Linux, sysctl and system_profiler on macOS, nvidia-smi and the CIM on Windows. A check
that fails leaves its field empty. It never stops the report.

bench() is an optional CPU test of about 1 s. It makes the fit rules honest on a computer with no GPU,
because the RAM alone does not tell the speed.
"""

from __future__ import annotations

import json
import logging
import os
import platform as _pf
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("raven.device")

GB = 1024**3
_cache: dict = {"at": 0.0, "info": None}
_lock = threading.Lock()
_bench: dict = {"gflops": None, "at": 0.0}


def system() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "windows"
    return sys.platform


def _run(cmd: list[str], timeout: float = 5) -> str:
    """The output of a command, or "" when it is missing or fails. No window on Windows."""
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=flags)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""


# ---------------------------------------------------------------- system and CPU


def _os_name() -> str:
    s = system()
    if s == "linux":
        try:
            kv = dict(ln.split("=", 1) for ln in Path("/etc/os-release").read_text().splitlines() if "=" in ln)
            return kv.get("PRETTY_NAME", "Linux").strip('"')
        except OSError:
            return "Linux"
    if s == "darwin":
        return f"macOS {_pf.mac_ver()[0]}".strip()
    if s == "windows":
        return f"Windows {_pf.release()} ({_pf.version()})"
    return _pf.system()


def _cpu_model() -> str:
    s = system()
    if s == "linux":
        try:
            for ln in Path("/proc/cpuinfo").read_text().splitlines():
                if ln.startswith(("model name", "Hardware", "Model")):
                    return ln.split(":", 1)[1].strip()
        except OSError:
            pass
    elif s == "darwin":
        out = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
        if out:
            return out
    elif s == "windows":
        try:
            import winreg  # type: ignore[import-not-found]

            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return _pf.processor() or _pf.machine()


def _cpu() -> dict:
    out = {"model": _cpu_model(), "cores": None, "threads": os.cpu_count() or 1}
    try:
        import psutil

        out["cores"] = psutil.cpu_count(logical=False)
    except Exception:  # noqa: BLE001
        pass
    return out


def _ram() -> dict:
    try:
        import psutil

        vm = psutil.virtual_memory()
        return {"total": int(vm.total), "free": int(vm.available)}
    except Exception:  # noqa: BLE001
        pass
    try:  # Linux without psutil
        kv = {ln.split(":")[0]: int(ln.split()[1]) * 1024 for ln in Path("/proc/meminfo").read_text().splitlines() if ln.split()[1:2]}
        return {"total": kv.get("MemTotal", 0), "free": kv.get("MemAvailable", 0)}
    except (OSError, ValueError, IndexError):
        return {"total": 0, "free": 0}


# ---------------------------------------------------------------- GPUs


def _nvidia() -> list[dict]:
    if not shutil.which("nvidia-smi"):
        return []
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    gpus = []
    for ln in out.strip().splitlines():
        try:
            name, mem = ln.rsplit(",", 1)
            gpus.append({"name": name.strip(), "vendor": "nvidia", "vram": int(float(mem)) * 1024 * 1024, "shared": False})
        except ValueError:
            continue
    return gpus


_PCI_VENDORS = {"0x10de": "nvidia", "0x1002": "amd", "0x8086": "intel"}


def _linux_drm(skip_nvidia: bool) -> list[dict]:
    """AMD and Intel GPUs from /sys/class/drm. AMD writes its VRAM size there. Intel shares the RAM."""
    gpus = []
    base = Path("/sys/class/drm")
    try:
        cards = sorted(p for p in base.iterdir() if re.fullmatch(r"card\d+", p.name))
    except OSError:
        return []
    names = _lspci_names()
    for card in cards:
        dev = card / "device"
        try:
            vendor = _PCI_VENDORS.get((dev / "vendor").read_text().strip())
        except OSError:
            continue
        if not vendor or (vendor == "nvidia" and skip_nvidia):
            continue
        vram = None
        try:
            vram = int((dev / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            pass
        slot = dev.resolve().name  # 0000:03:00.0
        name = names.get(slot.split(":", 1)[-1]) or f"{vendor.upper() if vendor == 'amd' else vendor.title()} GPU"
        # An AMD APU reports a small VRAM carve-out (512 MB) and shares the RAM.
        shared = vendor == "intel" or (vram is not None and vram < 2 * GB)
        gpus.append({"name": name, "vendor": vendor, "vram": vram, "shared": shared})
    return gpus


def _lspci_names() -> dict[str, str]:
    if not shutil.which("lspci"):
        return {}
    out = _run(["lspci", "-mm"])
    names = {}
    for ln in out.splitlines():
        parts = re.findall(r'"([^"]*)"', ln)
        slot = ln.split(" ", 1)[0]
        if len(parts) >= 3 and ("VGA" in parts[0] or "3D" in parts[0] or "Display" in parts[0]):
            name = f"{parts[1]} {parts[2]}".replace("Advanced Micro Devices, Inc. [AMD/ATI]", "AMD").replace("Corporation", "")
            names[slot] = re.sub(r"\s+", " ", name).strip()
    return names


def _mac_gpus(ram_total: int) -> list[dict]:
    if _pf.machine() == "arm64":
        # Apple silicon: one memory for the CPU and the GPU. macOS lets the GPU use about 2/3 of it (3/4 from 36 GB).
        chip = _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or "Apple silicon"
        share = 0.75 if ram_total >= 36 * GB else 0.66
        return [{"name": f"{chip} GPU", "vendor": "apple", "vram": int(ram_total * share), "shared": True}]
    out = _run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=10)
    gpus = []
    try:
        for g in json.loads(out or "{}").get("SPDisplaysDataType", []):
            name = g.get("sppci_model") or g.get("_name") or "GPU"
            mem = str(g.get("spdisplays_vram") or g.get("spdisplays_vram_shared") or "")
            m = re.match(r"(\d+)\s*(MB|GB)", mem)
            vram = int(m.group(1)) * (GB if m.group(2) == "GB" else 1024 * 1024) if m else None
            vendor = "amd" if "AMD" in name or "Radeon" in name else "intel" if "Intel" in name else "nvidia" if "NVIDIA" in name else "other"
            gpus.append({"name": name, "vendor": vendor, "vram": vram, "shared": vendor == "intel"})
    except (ValueError, AttributeError):
        pass
    return gpus


def _windows_gpus(skip_nvidia: bool) -> list[dict]:
    """The CIM list of video controllers. AdapterRAM is a 32-bit value (4 GB at most), so the registry size
    (qwMemorySize) wins when it is there. NVIDIA comes from nvidia-smi."""
    script = (
        "$r=@(); foreach($k in Get-ChildItem 'HKLM:\\SYSTEM\\ControlSet001\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}' -EA 0)"
        "{ $p=Get-ItemProperty $k.PSPath -EA 0; if($p.DriverDesc){ $r+=[pscustomobject]@{Name=$p.DriverDesc;"
        "Mem=$p.'HardwareInformation.qwMemorySize'} } }; $r | ConvertTo-Json -Compress"
    )
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=10)
    gpus = []
    try:
        rows = json.loads(out or "[]")
        for g in rows if isinstance(rows, list) else [rows]:
            name = str(g.get("Name") or "GPU")
            vendor = "nvidia" if "NVIDIA" in name else "amd" if ("AMD" in name or "Radeon" in name) else "intel" if "Intel" in name else "other"
            if vendor == "other" and ("Basic" in name or "Virtual" in name or "Remote" in name):
                continue
            if vendor == "nvidia" and skip_nvidia:
                continue
            vram = int(g["Mem"]) if isinstance(g.get("Mem"), int) else None
            gpus.append({"name": name, "vendor": vendor, "vram": vram, "shared": vendor == "intel"})
    except (ValueError, TypeError, AttributeError):
        pass
    return gpus


def _gpus(ram_total: int) -> list[dict]:
    s = system()
    if s == "darwin":
        return _mac_gpus(ram_total)
    gpus = _nvidia()
    if s == "linux":
        gpus += _linux_drm(skip_nvidia=bool(gpus))
    elif s == "windows":
        gpus += _windows_gpus(skip_nvidia=bool(gpus))
    return gpus


# ---------------------------------------------------------------- disk, battery, Ollama


def _disk() -> dict:
    path = config.DATA_DIR
    try:
        u = shutil.disk_usage(path)
        return {"path": str(path), "free": u.free, "total": u.total}
    except OSError:
        return {"path": str(path), "free": 0, "total": 0}


def battery() -> dict | None:
    try:
        import psutil

        b = psutil.sensors_battery()
        if b is not None:
            return {"percent": round(b.percent), "on_battery": not b.power_plugged}
    except Exception:  # noqa: BLE001
        pass
    return None


# ---------------------------------------------------------------- the report


def info(fresh: bool = False) -> dict:
    """The device report. Cached for 60 s, because the GPU tools take up to a few seconds."""
    with _lock:
        if not fresh and _cache["info"] and time.monotonic() - _cache["at"] < 60:
            out = dict(_cache["info"])
            out["ram"] = _ram()
            out["battery"] = battery()
            out["disk"] = _disk()
            return out
        ram = _ram()
        gpus = _gpus(ram["total"])
        dedicated = [g for g in gpus if not g["shared"] and g["vram"]]
        out = {
            "os": {"system": system(), "name": _os_name(), "arch": _pf.machine()},
            "cpu": _cpu(),
            "ram": ram,
            "gpus": gpus,
            # The best GPU for a local model: dedicated VRAM, or Apple's shared memory.
            "gpu_memory": max([g["vram"] for g in dedicated] + [g["vram"] for g in gpus if g["vendor"] == "apple"] or [0]),
            "disk": _disk(),
            "battery": battery(),
            "bench": _bench["gflops"],
        }
        _cache.update(at=time.monotonic(), info=out)
        return dict(out)


def summary(d: dict | None = None) -> str:
    d = d or info()
    gpu = ", ".join(f"{g['name']}" + (f" {round(g['vram'] / GB)} GB" if g.get("vram") and not g["shared"] else " (shared memory)" if g["shared"] else "")
                    for g in d["gpus"]) or "no GPU found"
    return (f"{d['os']['name']} {d['os']['arch']}, {d['cpu']['model']} ({d['cpu']['threads']} threads), "
            f"{round(d['ram']['total'] / GB)} GB RAM, {gpu}, {round(d['disk']['free'] / GB)} GB free")


def bench(seconds: float = 1.0) -> float:
    """A short CPU test: float32 matrix products on all cores, in GFLOPS. The local models run the same work."""
    import numpy as np

    n = 384
    a = np.random.default_rng(1).random((n, n), dtype=np.float32)
    b = a.T.copy()
    a @ b  # warm-up
    done, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        a @ b
        done += 1
    gflops = round(2 * n**3 * done / (time.perf_counter() - t0) / 1e9, 1)
    _bench.update(gflops=gflops, at=time.time())
    with _lock:
        if _cache["info"]:
            _cache["info"]["bench"] = gflops
    try:
        from . import db

        db.set_setting("device_bench", gflops)
    except Exception:  # noqa: BLE001
        pass
    return gflops


def load_bench() -> None:
    try:
        from . import db

        v = db.get_setting("device_bench")
        if isinstance(v, (int, float)):
            _bench["gflops"] = float(v)
    except Exception:  # noqa: BLE001
        pass
