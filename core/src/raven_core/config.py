"""Paths and runtime settings for the sidecar."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

VERSION = "0.3.0-beta.2"

# A packaged build (PyInstaller) is a release build. Developer-only paths stay off in it (PLAN4 R1): the
# `claude -p` provider, which uses the user's claude.ai login, and the screenshot and test hooks.
FROZEN = bool(getattr(sys, "frozen", False))
DEV = not FROZEN

def private_dir(path: Path) -> Path:
    """Make a folder that only this user can open (PLAN3 P0-2). The index holds the text of every indexed
    file, the PII spans, the ledger and the settings. A folder that exists gets mode 0700 again."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":  # Windows: the profile folder gives access to this user only.
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


DATA_DIR = private_dir(Path(os.environ.get("RAVEN_DATA_DIR") or user_data_dir("raven", appauthor=False)))
DB_PATH = DATA_DIR / "raven.db"
THUMB_DIR = private_dir(DATA_DIR / "thumbs")


def thumb_file(path: str, st: os.stat_result, px: int) -> Path:
    """The cache file of a thumbnail: the path, the change time, the size and the pixel size make the key."""
    import hashlib

    key = hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{px}".encode()).hexdigest()
    return THUMB_DIR / f"{key}.webp"


def protect_data_files() -> None:
    """Mode 0600 for the database files that older versions made with 0644."""
    if os.name == "nt":
        return
    for p in DATA_DIR.glob("raven.db*"):
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass

OLLAMA_URL = os.environ.get("RAVEN_OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("RAVEN_EMBED_MODEL", "embeddinggemma")

# Folder and file names that the indexer never enters (section 9.1).
EXCLUDE_NAMES = {
    ".ssh", ".gnupg", ".password-store", ".pki", ".mozilla", ".thunderbird",
    "node_modules", "__pycache__", ".git", ".venv", "venv", ".cache", ".npm",
    ".cargo", ".rustup", ".local", ".config", ".var", "Trash", ".Trash-1000",
}
# Windows and macOS system and app folders. Only there: on Linux a "Library" folder can hold books.
if os.name == "nt":
    EXCLUDE_NAMES |= {"AppData", "$Recycle.Bin", "System Volume Information"}
elif os.uname().sysname == "Darwin":
    EXCLUDE_NAMES |= {"Library", ".Trash"}
# Browser profile folders under ~/.config are covered by ".config".

# Text extraction limits.
MAX_TEXT_FILE = 2 * 1024 * 1024
MAX_DOC_FILE = 60 * 1024 * 1024

TEXT_EXTS = {
    "txt", "md", "markdown", "rst", "csv", "tsv", "json", "yaml", "yml", "toml", "ini", "cfg",
    "log", "py", "js", "ts", "tsx", "jsx", "c", "h", "cpp", "hpp", "java", "kt", "go", "rs",
    "rb", "php", "sh", "bash", "zsh", "sql", "html", "htm", "css", "scss", "xml", "tex", "lua",
    "dart", "swift", "r", "m", "cs", "vue", "svelte",
}
DOC_EXTS = {"pdf", "docx", "pptx", "xlsx", "odt", "ods", "odp", "epub"}
IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tif", "tiff", "avif", "heic", "jxl", "svg", "ico"}

# Model files (SigLIP 2, reranker, Privacy Filter, whisper). The repo folder in development,
# the user data folder in a packaged app (first run downloads them there).
_REPO_MODELS = Path(__file__).resolve().parents[3] / "models"
MODELS_DIR = Path(os.environ.get("RAVEN_MODELS_DIR") or (_REPO_MODELS if _REPO_MODELS.is_dir() else DATA_DIR / "models"))
