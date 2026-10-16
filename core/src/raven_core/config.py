"""Paths and runtime settings for the sidecar."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_dir

VERSION = "0.2.0"

DATA_DIR = Path(os.environ.get("RAVEN_DATA_DIR") or user_data_dir("raven", appauthor=False))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "raven.db"
THUMB_DIR = DATA_DIR / "thumbs"
THUMB_DIR.mkdir(parents=True, exist_ok=True)

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
