"""Start the sidecar: python -m raven_core --port 0, with the token in RAVEN_TOKEN.

--token also works, for tests and scripts. The app does not use it, because every user can read the
command line of a process (PLAN3 P0-1).
"""

from __future__ import annotations

import argparse
import logging
import os
import secrets


def _watch_parent() -> None:
    """Exit when the app that started this sidecar is gone. Without it, a killed app leaves the sidecar
    running and indexing (Ctrl+C on `pnpm dev` stops Electron without its quit handler)."""
    import threading
    import time

    pid = int(os.environ.get("RAVEN_PARENT_PID") or 0)
    if not pid or os.name == "nt":
        return

    def run():
        while True:
            time.sleep(2)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass

    threading.Thread(target=run, name="parent-watch", daemon=True).start()


_lock_file = None


def _lock_data_dir() -> None:
    """One sidecar for each data folder (PLAN3 P0-6). Two sidecars on one database run two indexers that
    write the same rows. The OS frees the lock when the process ends, also after a crash."""
    global _lock_file
    from . import config

    _lock_file = os.fdopen(os.open(config.DATA_DIR / "sidecar.lock", os.O_RDWR | os.O_CREAT, 0o600), "r+")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f"raven-core: another sidecar uses {config.DATA_DIR}. Close it first.") from None


def main() -> None:
    # A crash in native code (PyMuPDF, onnxruntime) prints the Python stack of every thread.
    import faulthandler

    faulthandler.enable()
    ap = argparse.ArgumentParser(prog="raven-core")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("RAVEN_PORT", "8765")))
    ap.add_argument("--token", default=os.environ.get("RAVEN_TOKEN"))
    ap.add_argument("--mcp", action="store_true", help="run the MCP stdio adapter (an MCP client starts this)")
    ap.add_argument("--elevated", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.elevated is not None:
        # A packaged Raven runs its elevated helper through itself (fs/elevate.py).
        import sys

        from .fs import elevate_helper

        sys.argv = ["elevate_helper", args.elevated]
        elevate_helper.main()
        return
    if args.mcp:
        from .mcp_server import main as mcp_main

        mcp_main()
        return
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("raven-core binds to the loopback address only")
    token = args.token or secrets.token_hex(16)
    os.environ["RAVEN_TOKEN"] = token
    os.environ["RAVEN_PORT"] = str(args.port)
    if not args.token:
        print(f"RAVEN_TOKEN={token}", flush=True)

    # No global umask: the sidecar also writes the user's own files (copies, conversions, extracts), and those
    # keep the normal modes. The 0700 data folder keeps other users out of everything inside it (PLAN3 P0-2).
    from . import config

    config.protect_data_files()
    _lock_data_dir()
    _watch_parent()
    from . import logs

    logs.setup(logging.INFO)  # the console and <data dir>/logs (PLAN4 B5)
    # watchfiles logs each batch at INFO ("11 changes detected"). The watchers handle the batches.
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    import uvicorn

    from .app import app

    # log_config=None: uvicorn errors go to the root handlers, so they reach the log file.
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False, log_config=None)


if __name__ == "__main__":
    main()
