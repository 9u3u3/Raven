"""The log file and the diagnostics report (PLAN4 B5).

The sidecar writes its log to <data dir>/logs/raven-core.log: 5 files of 5 MB each, then the oldest goes.
The log holds events and errors, never file content and never a cloud payload. A long message is cut.

"Copy diagnostics" in Settings builds one text report: the version, the system, the device, the feature
states, the settings without keys, and the last log lines. The report puts "~" in place of the home folder
and leaves out the user name.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform as _pf
import sys
import threading
import time
from pathlib import Path

from . import config

LOG_DIR = config.DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "raven-core.log"
CRASH_FILE = LOG_DIR / "crash.log"
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 4
MAX_MESSAGE = 2000
# Settings that the report leaves out: keys, tab state and the user's own lists.
_SKIP = {"ui_tabs", "galaxy", "favorites", "claude_config_dir", "features_seen"}

_crash = None


class _Cut(logging.Filter):
    """Cut a long message, so a log line cannot carry a large piece of a file."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if len(msg) > MAX_MESSAGE:
            record.msg, record.args = msg[:MAX_MESSAGE] + " …(cut)", ()
        return True


def setup(level: int = logging.INFO) -> None:
    """Log to the console and to the rotating log file. Errors in threads and native crashes go there too."""
    global _crash
    config.private_dir(LOG_DIR)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.addFilter(_Cut())
    root.addHandler(console)
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
        fh.setFormatter(fmt)
        fh.addFilter(_Cut())
        root.addHandler(fh)
        if os.name != "nt":
            os.chmod(LOG_FILE, 0o600)
    except OSError as e:
        root.warning("log file: %s", e)
    # httpx logs every request at INFO: the Ollama checks would fill the log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    log = logging.getLogger("raven")

    def on_thread_error(args: threading.ExceptHookArgs) -> None:
        log.error("thread %s: %s", args.thread.name if args.thread else "?", args.exc_value,
                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    def on_error(kind, value, tb) -> None:
        log.critical("unhandled: %s", value, exc_info=(kind, value, tb))

    threading.excepthook = on_thread_error
    sys.excepthook = on_error
    # A crash in native code (PyMuPDF, onnxruntime) prints the Python stack of every thread to crash.log.
    try:
        import faulthandler

        _crash = open(CRASH_FILE, "a", encoding="utf-8")  # noqa: SIM115 - open for the life of the process
        if os.name != "nt":
            os.chmod(CRASH_FILE, 0o600)
        if CRASH_FILE.stat().st_size > MAX_BYTES:
            _crash.truncate(0)
        _crash.write(f"--- start {time.strftime('%Y-%m-%d %H:%M:%S')} pid {os.getpid()}\n")
        _crash.flush()
        faulthandler.enable(file=_crash)
    except OSError:
        pass
    log.info("raven-core %s on %s %s, data in %s", config.VERSION, sys.platform, _pf.machine(), _home(str(config.DATA_DIR)))


def _home(text: str) -> str:
    """Put "~" in place of the home folder and "<user>" in place of the user name."""
    home = str(Path.home())
    if home and home not in ("/", "\\"):
        text = text.replace(home, "~")
    user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) >= 3:
        text = text.replace(user, "<user>")
    return text


def tail(path: Path, lines: int) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            data = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    return data.splitlines()[-lines:]


def diagnostics(lines: int = 300) -> str:
    """One text report for a bug report. It holds no file content, no key and no payload."""
    from . import db, device, features

    out: list[str] = [f"Raven {config.VERSION}", time.strftime("%Y-%m-%d %H:%M:%S %z")]
    out.append(f"System: {_pf.system()} {_pf.release()} ({_pf.machine()}), Python {_pf.python_version()}, "
               f"{'release' if not config.DEV else 'development'} build")
    try:
        d = device.info()
        out.append("Device: " + device.summary(d))
    except Exception as e:  # noqa: BLE001 - a report must not fail
        out.append(f"Device: error {e}")
    try:
        out.append("")
        out.append("Features:")
        for f in features.status()["features"]:
            out.append(f"  {f['id']}: {f['state']}, fit {f['fit']['level']} ({f['fit']['reason']})")
    except Exception as e:  # noqa: BLE001
        out.append(f"  error {e}")
    try:
        from . import keys

        rows = db.conn().execute("SELECT key, value FROM settings").fetchall()
        out.append("")
        out.append("Settings:")
        for k, v in rows:
            if k in _SKIP or k in keys.NAMES or k.endswith("_api_key"):
                continue
            out.append(f"  {k} = {v[:120]}")
        out.append(f"  keys stored: {', '.join(k for k in keys.NAMES if keys.get(k)) or 'none'}")
    except Exception as e:  # noqa: BLE001
        out.append(f"  error {e}")
    try:
        c = db.conn()
        files = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        chunks = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        out.append("")
        out.append(f"Index: {files} items, {chunks} chunks")
    except Exception as e:  # noqa: BLE001
        out.append(f"Index: error {e}")
    for name, path in (("Sidecar log", LOG_FILE), ("Crash log", CRASH_FILE),
                       ("App log", Path(os.environ["RAVEN_MAIN_LOG"]) if os.environ.get("RAVEN_MAIN_LOG") else None)):
        if path is None:
            continue
        out.append("")
        out.append(f"{name} (last lines):")
        out += ["  " + ln for ln in tail(path, lines if name != "Crash log" else 60)]
    return _home("\n".join(out))


def info() -> dict:
    return {"dir": str(LOG_DIR), "file": str(LOG_FILE), "main": os.environ.get("RAVEN_MAIN_LOG") or ""}
