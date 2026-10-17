"""Install Ollama for the user (PLAN4 section 5.3.1).

"Install for me" downloads the official Ollama build from the GitHub releases of ollama/ollama, checks the
SHA-256 that GitHub gives for each file, and unpacks it into <data dir>/ollama. It needs no administrator
rights on any system, and it touches nothing outside that folder. The Electron main process then runs
`ollama serve` from there (it asks GET /ollama for the path).

Linux    ollama-linux-<arch>.tar.zst (and the ROCm part for an AMD GPU)
macOS    ollama-darwin.tgz (the command-line build, Apple silicon and Intel)
Windows  ollama-windows-<arch>.zip

"Show me the steps" gives the official install steps of each system instead. A system install of Ollama
(the user's own, or one that ran before Raven) always wins over the copy in the data folder.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform as _pf
import shutil
import stat
import tarfile
import threading
import time
import zipfile
from pathlib import Path

import httpx

from . import config, events

log = logging.getLogger("raven.ollama")

RELEASES = "https://api.github.com/repos/ollama/ollama/releases/latest"
HOME = config.DATA_DIR / "ollama"
MARKER = HOME / "raven-install.json"

STEPS = {
    "linux": [
        "Open a terminal.",
        "Run the official install script. It asks for your password, because it installs a system service.",
        "Come back to Raven and press Check again.",
    ],
    "darwin": [
        "Download Ollama for macOS from ollama.com/download/mac.",
        "Open the file, then move Ollama to Applications.",
        "Open Ollama once. Its icon shows in the menu bar.",
        "Come back to Raven and press Check again.",
    ],
    "windows": [
        "Download OllamaSetup.exe from ollama.com/download/windows.",
        "Run it. It installs for your user only.",
        "Come back to Raven and press Check again.",
    ],
}
COMMANDS = {"linux": "curl -fsSL https://ollama.com/install.sh | sh", "darwin": "https://ollama.com/download/mac",
            "windows": "https://ollama.com/download/windows"}

_job: dict = {"state": None, "done": 0, "total": 0, "error": None, "step": ""}
_release: dict = {"at": 0.0, "data": None}
_lock = threading.Lock()
_ver: dict = {"at": 0.0, "running": False, "version": None}


def _system() -> str:
    from .device import system

    return system()


def _arch() -> str:
    m = _pf.machine().lower()
    return "arm64" if m in ("arm64", "aarch64") else "amd64"


def managed_binary() -> Path | None:
    try:
        p = Path(json.loads(MARKER.read_text(encoding="utf-8"))["binary"])
        return p if p.is_file() else None
    except (OSError, ValueError, KeyError):
        return None


def system_binary() -> str | None:
    """An Ollama that the user installed: on PATH, or in the usual places of each system."""
    found = shutil.which("ollama")
    if found:
        return found
    for p in ("/usr/local/bin/ollama", "/usr/bin/ollama", os.path.expanduser("~/.local/bin/ollama"),
              "/Applications/Ollama.app/Contents/Resources/ollama",
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")):
        if "%" not in p and os.path.isfile(p):
            return p
    return None


def binary() -> str | None:
    """The Ollama program to start: the system one first, else the copy in the data folder."""
    own = system_binary()
    if own:
        return own
    m = managed_binary()
    return str(m) if m else None


def _version() -> tuple[bool, str | None]:
    """Is the server up, and its version. Cached for 5 s."""
    now = time.monotonic()
    if now - _ver["at"] < 5:
        return _ver["running"], _ver["version"]
    try:
        r = httpx.get(f"{config.OLLAMA_URL}/api/version", timeout=2)
        running, version = r.status_code == 200, r.json().get("version")
    except Exception:  # noqa: BLE001
        running, version = False, None
    _ver.update(at=now, running=running, version=version)
    return running, version


def _wanted_assets(assets: dict[str, dict]) -> list[str]:
    s, arch = _system(), _arch()
    if s == "linux":
        names = [f"ollama-linux-{arch}.tar.zst"]
        if arch == "amd64":
            from . import device

            if any(g["vendor"] == "amd" and not g["shared"] for g in device.info()["gpus"]):
                names.append("ollama-linux-amd64-rocm.tar.zst")  # the ROCm libraries go on top of the base
    elif s == "darwin":
        names = ["ollama-darwin.tgz"]
    elif s == "windows":
        names = [f"ollama-windows-{arch}.zip"]
    else:
        return []
    return [n for n in names if n in assets]


def release(force: bool = False) -> dict | None:
    """The latest Ollama release: its version and the files for this computer. Cached for an hour."""
    with _lock:
        if not force and _release["data"] and time.time() - _release["at"] < 3600:
            return _release["data"]
    try:
        r = httpx.get(RELEASES, timeout=10, headers={"Accept": "application/vnd.github+json"}, follow_redirects=True)
        r.raise_for_status()
        j = r.json()
    except Exception as e:  # noqa: BLE001
        log.info("ollama release: %s", e)
        return None
    assets = {a["name"]: {"url": a["browser_download_url"], "size": a.get("size") or 0,
                          "sha256": (a.get("digest") or "").removeprefix("sha256:")} for a in j.get("assets", [])}
    names = _wanted_assets(assets)
    data = {"version": str(j.get("tag_name") or "").lstrip("v"), "files": [{"name": n, **assets[n]} for n in names]}
    with _lock:
        _release.update(at=time.time(), data=data)
    return data


def status(quick: bool = False) -> dict:
    """Installed or not, running or not, the install job, and the steps for this system.
    quick: no request to GitHub (the download size is then unknown)."""
    running, version = _version()
    own = system_binary()
    managed = managed_binary()
    s = _system()
    rel = None if quick else release()
    return {
        "installed": bool(own or managed), "running": running, "version": version,
        "path": own or (str(managed) if managed else None), "managed": bool(managed and not own),
        "system": s, "can_install": s in ("linux", "darwin", "windows"),
        "download": ({"version": rel["version"], "bytes": sum(f["size"] for f in rel["files"])} if rel and rel["files"] else None),
        "steps": STEPS.get(s, []), "command": COMMANDS.get(s, "https://ollama.com/download"),
        "job": dict(_job) if _job["state"] else None,
    }


def _emit() -> None:
    events.publish("ollama_install", job=dict(_job))


def _download(c: httpx.Client, f: dict, folder: Path) -> Path:
    out = folder / f["name"]
    part = out.with_name(out.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with c.stream("GET", f["url"], headers=headers) as r:
        if r.status_code == 200:
            have = 0  # the server sends the whole file again
        elif r.status_code != 206:
            r.raise_for_status()
        _job["done"] += have
        with open(part, "ab" if have else "wb") as fh:
            last = 0
            for chunk in r.iter_bytes(1024 * 1024):
                if _job.get("cancel"):
                    raise RuntimeError("cancelled")
                fh.write(chunk)
                _job["done"] += len(chunk)
                if _job["done"] - last > 16 * 1024 * 1024:
                    last = _job["done"]
                    _emit()
    _job["step"] = f"Checking {f['name']}"
    _emit()
    if f.get("sha256"):
        h = hashlib.sha256()
        with open(part, "rb") as fh:
            for block in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != f["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"{f['name']}: the SHA-256 does not match. Raven deleted the file.")
    part.replace(out)
    return out


def _unpack(archive: Path, dest: Path) -> None:
    """Unpack into dest. The "data" filter refuses members that point outside dest, device files and
    setuid bits."""
    name = archive.name
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            root = dest.resolve()
            for m in z.infolist():
                target = (dest / m.filename).resolve()
                if not target.is_relative_to(root):
                    raise RuntimeError(f"{name}: a member points outside the folder")
            z.extractall(dest)
        return
    if name.endswith(".tar.zst"):
        import zstandard

        with open(archive, "rb") as fh, zstandard.ZstdDecompressor().stream_reader(fh) as zr, \
                tarfile.open(fileobj=zr, mode="r|") as t:
            t.extractall(dest, filter="data")
        return
    with tarfile.open(archive, "r:*") as t:
        t.extractall(dest, filter="data")


def _find_binary(dest: Path) -> Path | None:
    exe = "ollama.exe" if _system() == "windows" else "ollama"
    for cand in (dest / "bin" / exe, dest / exe):
        if cand.is_file():
            return cand
    for p in dest.rglob(exe):
        if p.is_file():
            return p
    return None


def _install() -> None:
    try:
        rel = release(force=True)
        if not rel or not rel["files"]:
            raise RuntimeError("No Ollama build for this system on GitHub. Use the steps instead.")
        need = sum(f["size"] for f in rel["files"])
        free = shutil.disk_usage(config.DATA_DIR).free
        # The archive and the unpacked files are on disk at the same time for a moment.
        if free < need * 3 + 1024**3:
            raise RuntimeError(f"Not enough free disk space: Ollama needs about {round(need * 3 / 1024**3, 1)} GB, "
                               f"{round(free / 1024**3, 1)} GB is free.")
        _job.update(total=need, done=0)
        folder = config.private_dir(HOME / ".download")
        stage = HOME / f"ollama-{rel['version']}.new"
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60, connect=10)) as c:
            archives = []
            for f in rel["files"]:
                _job["step"] = f"Downloading {f['name']}"
                _emit()
                archives.append(_download(c, f, folder))
        for a in archives:
            _job["step"] = f"Unpacking {a.name}"
            _emit()
            _unpack(a, stage)
        exe = _find_binary(stage)
        if not exe:
            raise RuntimeError("The Ollama program is not in the download")
        if os.name != "nt":
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        final = HOME / f"ollama-{rel['version']}"
        shutil.rmtree(final, ignore_errors=True)
        stage.rename(final)
        binary_path = final / exe.relative_to(stage)
        MARKER.write_text(json.dumps({"version": rel["version"], "binary": str(binary_path), "at": int(time.time())}), encoding="utf-8")
        # Old versions and the archives go: the models live in ~/.ollama and stay.
        for old in HOME.glob("ollama-*"):
            if old != final and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(folder, ignore_errors=True)
        _job.update(state="done", step=f"Ollama {rel['version']} is installed", error=None)
        log.info("ollama %s installed in %s", rel["version"], final)
    except Exception as e:  # noqa: BLE001
        _job.update(state="error", error=str(e)[:300])
        log.warning("ollama install: %s", e)
    _emit()


def install() -> dict:
    """Start the install in the background. Progress comes as "ollama_install" events."""
    with _lock:
        if _job["state"] == "running":
            return status(quick=True)
        _job.clear()
        _job.update(state="running", done=0, total=0, error=None, step="Looking for the latest Ollama")
    threading.Thread(target=_install, name="ollama-install", daemon=True).start()
    return status(quick=True)


def cancel() -> None:
    _job["cancel"] = True
