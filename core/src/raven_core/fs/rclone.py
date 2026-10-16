"""rclone for WebDAV, S3, SFTP and cloud drives (sections 9.12 and 13.12).

Raven uses the user's own rclone config, so remotes that the user made with `rclone config` show too.
A remote mounts with `rclone mount` (FUSE) under <data dir>/mnt/<name>. The mount then appears in the left
column under Network (listing.places reads fuse.rclone mounts), and Raven browses it like a folder.
Network folders get metadata only, unless the user adds the folder to the index (9.12).

Passwords go to rclone with --obscure. Raven does not store them. Cloud drives (Google Drive, Dropbox,
OneDrive) use rclone's browser sign-in: rclone opens the browser and waits for the user.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .. import db, events
from ..config import DATA_DIR

MNT = DATA_DIR / "mnt"
NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_. -]{0,40}$")

# The forms that Raven shows. Other types still work when the user made them with `rclone config`.
TYPES: dict[str, dict] = {
    "webdav": {"label": "WebDAV (Nextcloud, ownCloud, a NAS)", "fields": ["url", "vendor", "user", "pass"]},
    "s3": {"label": "S3 (AWS, MinIO, Cloudflare R2, Wasabi)", "fields": ["provider", "access_key_id", "secret_access_key", "endpoint", "region"]},
    "sftp": {"label": "SFTP (a computer with SSH)", "fields": ["host", "user", "port", "pass", "key_file"]},
    "drive": {"label": "Google Drive (browser sign-in)", "fields": [], "oauth": True},
    "dropbox": {"label": "Dropbox (browser sign-in)", "fields": [], "oauth": True},
    "onedrive": {"label": "OneDrive (browser sign-in)", "fields": [], "oauth": True},
}
SECRET = {"pass", "secret_access_key"}
_auth: dict[str, dict] = {}


_LOG_PREFIX = re.compile(r"^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d [A-Z]+\s*:\s*")


def _last_line(text: str) -> str:
    """The last line of rclone output, without its log time and level."""
    lines = [ln for ln in (text or "").strip().splitlines() if ln.strip()]
    return _LOG_PREFIX.sub("", lines[-1]) if lines else ""


class RcloneError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def binary() -> str | None:
    p = os.environ.get("RAVEN_RCLONE") or db.get_setting("rclone_path") or shutil.which("rclone")
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else None


def _need() -> str:
    b = binary()
    if not b:
        raise RcloneError("rclone is not installed. Install it (Arch: pacman -S rclone, Debian or Ubuntu: apt install rclone, "
                          "or rclone.org/install), then open this dialog again.", 424)
    return b


def _run(args: list[str], timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run([_need(), *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    if check and r.returncode != 0:
        raise RcloneError(f"rclone: {_last_line(r.stderr or r.stdout) or r.returncode}"[:300])
    return r


def mountpoint(name: str) -> Path:
    return MNT / name


def _mounted(path: Path) -> bool:
    try:
        mounts = Path("/proc/mounts").read_text()
    except OSError:
        return os.path.ismount(path)
    return any(line.split()[1].replace("\\040", " ") == str(path) for line in mounts.splitlines() if len(line.split()) > 1)


def status() -> dict:
    b = binary()
    if not b:
        return {"available": False, "remotes": [], "types": TYPES}
    out = _run(["config", "dump"], timeout=20)
    conf = json.loads(out.stdout or "{}")
    remotes = []
    for name, c in sorted(conf.items()):
        mp = mountpoint(name)
        remotes.append({"name": name, "type": c.get("type", "?"), "mounted": _mounted(mp), "path": str(mp),
                        "label": TYPES.get(c.get("type", ""), {}).get("label", c.get("type", ""))})
    version = _run(["version"], timeout=10).stdout.splitlines()[0]
    return {"available": True, "version": version, "remotes": remotes, "types": TYPES, "auth": _auth,
            "fuse": bool(shutil.which("fusermount3") or shutil.which("fusermount"))}


def create(name: str, typ: str, params: dict[str, str]) -> dict:
    if not NAME.match(name):
        raise RcloneError("A name has letters, digits, spaces, dots, _ or -, up to 40 characters")
    if typ not in TYPES:
        raise RcloneError(f"Raven makes {', '.join(TYPES)} remotes. Use `rclone config` for other types.")
    if any(r["name"] == name for r in status()["remotes"]):
        raise RcloneError(f"A remote named {name} exists")
    args = ["config", "create", name, typ]
    for k in TYPES[typ]["fields"]:
        v = (params.get(k) or "").strip()
        if v:
            args += [k, v]
    if any((params.get(k) or "").strip() for k in SECRET):
        args.append("--obscure")
    if TYPES[typ].get("oauth"):
        # rclone opens the browser for the sign-in and waits. Run it in the background and report the result.
        _auth[name] = {"state": "waiting", "error": None}

        def work():
            try:
                _run(args, timeout=600)
                _auth[name] = {"state": "done", "error": None}
            except (RcloneError, subprocess.TimeoutExpired) as e:
                _auth[name] = {"state": "error", "error": str(e)[:300]}
                _run(["config", "delete", name], check=False)
            events.publish("rclone_changed", name=name)

        threading.Thread(target=work, name="rclone-auth", daemon=True).start()
        return {"name": name, "state": "waiting", "note": "Sign in in the browser window that rclone opened."}
    _run(args)
    # A quick check that the remote answers. A wrong password shows now, not at mount time.
    r = _run(["lsf", f"{name}:", "--max-depth", "1", "--dirs-only"], timeout=30, check=False)
    if r.returncode != 0:
        _run(["config", "delete", name], check=False)
        raise RcloneError(f"The remote does not answer: {_last_line(r.stderr) or r.returncode}"[:300])
    return {"name": name, "state": "done"}


def mount(name: str) -> dict:
    b = _need()
    if not (shutil.which("fusermount3") or shutil.which("fusermount")):
        raise RcloneError("FUSE is not installed, so rclone cannot mount. Install fuse3.", 424)
    mp = mountpoint(name)
    if _mounted(mp):
        return {"path": str(mp)}
    mp.mkdir(parents=True, exist_ok=True)
    cache = DATA_DIR / "rclone-cache"
    r = subprocess.run(
        [b, "mount", f"{name}:", str(mp), "--daemon", "--vfs-cache-mode", "writes", "--dir-cache-time", "30s",
         "--cache-dir", str(cache), "--volname", name],
        capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
    )
    if r.returncode != 0:
        raise RcloneError(f"rclone mount failed: {_last_line(r.stderr or r.stdout) or r.returncode}"[:300])
    for _ in range(50):
        if _mounted(mp):
            events.publish("rclone_changed", name=name)
            return {"path": str(mp)}
        time.sleep(0.1)
    raise RcloneError("The mount did not appear in 5 s")


def unmount(name: str) -> None:
    mp = mountpoint(name)
    if not _mounted(mp):
        return
    tool = shutil.which("fusermount3") or shutil.which("fusermount")
    r = subprocess.run([tool, "-u", str(mp)], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RcloneError(f"Unmount failed: {r.stderr.strip()[-200:]}. Close the files that are open there.")
    events.publish("rclone_changed", name=name)


def delete(name: str) -> None:
    unmount(name)
    _run(["config", "delete", name])
    try:
        mountpoint(name).rmdir()
    except OSError:
        pass
