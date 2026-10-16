"""Start the sidecar: python -m raven_core --port 0 --token <token>."""

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

    _watch_parent()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    import uvicorn

    from .app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
