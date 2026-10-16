"""Operating system differences in one place (section 13.12: Windows and macOS).

Linux is the tested target. The Windows and macOS branches follow the documented OS behavior. They are
not tested on a real Windows or macOS machine yet (the CI workflow builds them; see the README).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def trim_memory() -> None:
    """Give freed memory back to the system after a model unloads (glibc keeps it otherwise)."""
    import gc

    gc.collect()
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except (OSError, AttributeError):
            pass


def owner_group(st: os.stat_result) -> tuple[str, str]:
    if IS_WIN:
        return "", ""
    import grp
    import pwd

    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return owner, group


def acl_summary(path: str) -> list[str] | None:
    """Windows: the ACL entries from icacls, one line each (Properties, section 9.13). Linux: the extended ACL
    entries from getfacl (PLAN2 P2-5), only when the file has more than the owner, group and others."""
    if IS_LINUX:
        if not shutil.which("getfacl"):
            return None
        try:
            r = subprocess.run(["getfacl", "-p", "--omit-header", "--skip-base", path], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        lines = [ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]
        return lines[:12] or None
    if not IS_WIN:
        return None
    try:
        r = subprocess.run(["icacls", path], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("Successfully")]
    if lines:
        lines[0] = lines[0][len(path):].strip() if lines[0].startswith(path) else lines[0]
    return lines[:12]


def mounts() -> list[tuple[str, str, str]]:
    """(device, mount point, file system type) for each mounted volume."""
    out: list[tuple[str, str, str]] = []
    if IS_LINUX:
        try:
            for line in Path("/proc/mounts").read_text().splitlines():
                f = line.split()
                if len(f) >= 3:
                    out.append((f[0], f[1].replace("\\040", " "), f[2]))
        except OSError:
            pass
    elif IS_MAC:
        try:
            r = subprocess.run(["mount"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                m = re.match(r"^(.+?) on (.+?) \(([^,)]+)", line)
                if m:
                    out.append((m.group(1), m.group(2), m.group(3)))
        except (OSError, subprocess.SubprocessError):
            pass
    elif IS_WIN:
        drives = os.listdrives() if hasattr(os, "listdrives") else [f"{c}:\\" for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
        for d in drives:
            out.append((d, d.rstrip("\\") + "/", "ntfs"))
    return out


def drive_kind(mnt: str, fstype: str) -> str | None:
    """'drive', 'removable' or None (not a place for the left column)."""
    if IS_MAC:
        if mnt == "/":
            return "drive"
        if mnt.startswith("/Volumes/"):
            return "removable" if fstype in ("msdos", "exfat", "vfat") else "drive"
        return None
    if IS_WIN:
        return "drive"
    return None


def trash_home() -> Path:
    """The trash that Raven writes. Linux: the FreeDesktop trash. Windows and macOS: Raven's own folder,
    because their system trash does not tell a program where an item went, and undo needs that."""
    if IS_LINUX:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return Path(base) / "Trash"
    from .config import DATA_DIR

    return DATA_DIR / "Trash"


def to_client(value):
    """Windows: paths in answers use / (the renderer splits paths at /, and Windows accepts C:/x and //host/share)."""
    if not IS_WIN:
        return value
    if isinstance(value, str):
        if re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\\\"):
            return value.replace("\\", "/")
        return value
    if isinstance(value, list):
        return [to_client(v) for v in value]
    if isinstance(value, dict):
        return {k: to_client(v) for k, v in value.items()}
    return value


def soffice() -> str | None:
    """LibreOffice: in PATH, or at its default place on Windows and macOS."""
    for name in ("soffice", "libreoffice"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe", r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(p):
            return p
    return None
