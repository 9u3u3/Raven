"""The system check for the setup screen: Raven core, Ollama, models, cloud keys and the optional tools.

Each tool row says what the tool adds and how to install it on this system. Raven works without the
optional tools, with fewer features. Nothing here installs anything: the user runs the commands.
"""

from __future__ import annotations

import os
import shutil
import sys


from . import config, db, platform

# (program, what it adds, package on Arch, on Debian or Ubuntu, on Fedora)
TOOLS: list[tuple[str, str, str, str, str]] = [
    ("soffice", "Convert Word, Excel and PowerPoint files to PDF", "libreoffice-fresh", "libreoffice", "libreoffice"),
    ("pandoc", "Convert Markdown and HTML documents", "pandoc", "pandoc", "pandoc"),
    ("ffmpeg", "Video frames, audio for transcripts, video conversion", "ffmpeg", "ffmpeg", "ffmpeg-free"),
    ("tesseract", "Read text in scans and screenshots (OCR)", "tesseract tesseract-data-eng", "tesseract-ocr", "tesseract"),
    ("magick", "Convert images between formats", "imagemagick", "imagemagick", "ImageMagick"),
    ("7z", "Create and open .7z archives", "7zip", "p7zip-full", "p7zip"),
    ("zstd", "Fast compression (.zst, .tar.zst)", "zstd", "zstd", "zstd"),
    ("cjxl", "Lossless JPEG XL photo compression", "libjxl", "libjxl-tools", "libjxl-utils"),
    ("avifenc", "AVIF photo compression (lossy, Suggest mode)", "libavif", "libavif-bin", "libavif-tools"),
    ("gio", "Network shares: Windows (SMB), SFTP, NFS, FTP, WebDAV", "gvfs gvfs-smb", "gvfs-backends gvfs-fuse", "gvfs gvfs-smb"),
    ("smbclient", "List the shared folders of a Windows PC", "smbclient", "smbclient", "samba-client"),
    ("rclone", "Cloud storage: Google Drive, Dropbox, OneDrive, S3", "rclone", "rclone", "rclone"),
    ("fusermount3", "Open cloud storage as a folder", "fuse3", "fuse3", "fuse3"),
    ("pkexec", "Retry an action as administrator", "polkit", "pkexec", "polkit"),
]

OLLAMA_INSTALL = {
    "linux": "curl -fsSL https://ollama.com/install.sh | sh",
    "darwin": "Download Ollama from https://ollama.com/download/mac",
    "win32": "Download Ollama from https://ollama.com/download/windows",
}


def _distro() -> str:
    from .fs.network import _distro as d

    return d() if sys.platform.startswith("linux") else ""


def _install(pkgs: list[str]) -> str:
    if not pkgs:
        return ""
    d = _distro()
    if d == "arch":
        return "sudo pacman -S --needed " + " ".join(pkgs)
    if d in ("debian", "ubuntu"):
        return "sudo apt install " + " ".join(pkgs)
    if d == "fedora":
        return "sudo dnf install " + " ".join(pkgs)
    if d == "suse":
        return "sudo zypper install " + " ".join(pkgs)
    return "Install: " + ", ".join(pkgs)


def _found(prog: str) -> bool:
    if prog == "soffice":
        return platform.soffice() is not None
    return shutil.which(prog) is not None


def ollama_installed() -> bool:
    if shutil.which("ollama"):
        return True
    for p in ("/usr/local/bin/ollama", "/usr/bin/ollama", os.path.expanduser("~/.local/bin/ollama"),
              "/Applications/Ollama.app", os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")):
        if os.path.exists(p):
            return True
    return False


def check() -> dict:
    from . import models
    from .router import cloud

    m = models.status()
    d = _distro()
    col = {"arch": 2, "debian": 3, "ubuntu": 3, "fedora": 4}.get(d)
    tools = []
    missing_pkgs: list[str] = []
    for prog, adds, *pkgs in TOOLS:
        ok = _found(prog)
        pkg = pkgs[col - 2] if col else ""
        if not ok and pkg:
            missing_pkgs += pkg.split()
        tools.append({"name": prog, "adds": adds, "ok": ok, "install": "" if ok else _install(pkg.split() if pkg else [prog])})
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    return {
        "core": {"version": config.VERSION, "data_dir": str(config.DATA_DIR), "models_dir": str(config.MODELS_DIR),
                 "platform": plat, "distro": d, "frozen": bool(getattr(sys, "frozen", False))},
        "ollama": {"installed": ollama_installed(), "running": m["ollama_running"], "url": config.OLLAMA_URL,
                   "install": OLLAMA_INSTALL.get(plat, "https://ollama.com/download")},
        "models": m,
        "tools": tools,
        "install_all": _install(list(dict.fromkeys(missing_pkgs))) if missing_pkgs else "",
        "cloud": {**cloud.configured(), "ai_mode": db.get_setting("ai_mode") or "hybrid"},
    }
